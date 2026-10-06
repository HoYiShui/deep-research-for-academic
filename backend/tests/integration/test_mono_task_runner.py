"""Actual PG scheduler with explicitly controlled executors, not paid research."""

import asyncio
from uuid import uuid4

import pytest

from application.errors import AppError
from application.settings import Settings
from application.task_runner import TaskRunner
from tests.integration.test_mono_run_lifecycle import cancel, ready
from tests.integration.test_mono_transactions import setup_store


def runner(store, execute, **options):
    return TaskRunner(
        store=store,
        execute=execute,
        settings=Settings(lease_s=3, heartbeat_s=1, scan_s=1, shutdown_s=1),
        **options,
    )


async def wait_for_status(store, owner, run_id, status):
    async with asyncio.timeout(5):
        while True:
            current = await store.research.get_run(owner, run_id)
            if current.status == status:
                return current
            await asyncio.sleep(0.02)


async def test_runner_claims_once_renews_and_stops_cancelled_execution(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(claimed, stop):
        calls.append(claimed.run.run_id)
        entered.set()
        await release.wait()
        async with store.transaction() as tx:
            await store.research.finish_cancelled(claimed, tx)

    worker = runner(store, execute)
    await worker.start()
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        original = await store.research.get_run(user.user_id, commit.run.run_id)
        async with asyncio.timeout(4):
            while (
                await store.research.get_run(user.user_id, commit.run.run_id)
            ).lease_expires_at <= original.lease_expires_at:
                await asyncio.sleep(0.02)
        await cancel(store, user.user_id, commit.session.session_id)
        release.set()
        await wait_for_status(store, user.user_id, commit.run.run_id, "cancelled")
        assert calls == [commit.run.run_id]
        assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1
    finally:
        await worker.aclose()


async def test_shutdown_records_interruption_and_does_not_restart_paid_work(pg_database):
    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    entered = asyncio.Event()

    async def execute(claimed, stop):
        entered.set()
        await asyncio.Event().wait()

    worker = runner(store, execute)
    await worker.start()
    await asyncio.wait_for(entered.wait(), timeout=5)
    await worker.aclose()
    run = await store.research.get_run(user.user_id, commit.run.run_id)
    assert run.status == "failed" and run.failure.code == "interrupted" and run.resume_allowed
    assert worker.closed and worker.active is None
    new_worker = runner(store, execute)
    await new_worker.tick()
    assert new_worker.active is None
    await new_worker.aclose()
    assert (await store.research.get_run(user.user_id, commit.run.run_id)).attempt_count == 1


async def test_lost_lease_aborts_inflight_executor_without_old_worker_terminal_write(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    entered, aborted = asyncio.Event(), asyncio.Event()

    async def execute(claimed, stop):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            aborted.set()

    worker = runner(store, execute)
    await worker.start()
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await pool.execute(
            "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
        await asyncio.wait_for(aborted.wait(), timeout=4)
        current = await wait_for_status(store, user.user_id, commit.run.run_id, "failed")
        assert current.failure.code == "interrupted" and current.attempt_count == 1
    finally:
        await worker.aclose()


async def test_executor_exception_is_observed_and_persisted_without_sensitive_message(pg_database):
    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)

    async def execute(claimed, stop):
        raise RuntimeError("secret-model-endpoint-and-key")

    worker = runner(store, execute)
    await worker.tick()
    current = await wait_for_status(store, user.user_id, commit.run.run_id, "failed")
    assert (
        current.failure.code == "execution_failed"
        and "secret" not in current.failure.model_dump_json()
    )
    await worker.aclose()


async def test_executor_return_without_terminal_is_not_completed(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)

    async def execute(claimed, stop):
        return None

    worker = runner(store, execute)
    await worker.tick()
    current = await wait_for_status(store, user.user_id, commit.run.run_id, "failed")
    assert current.failure.code == "invalid_execution_result"
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    await worker.aclose()


@pytest.mark.parametrize(
    "code,resumable",
    [("tool_call_uncertain", True), ("config_unavailable", False), ("budget_exhausted", False)],
)
async def test_known_execution_reason_is_preserved_without_exposing_error_text(
    pg_database, code, resumable
):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)

    async def execute(claimed, stop):
        raise AppError(code, "secret-provider-request-and-key")

    worker = runner(store, execute)
    try:
        await worker.tick()
        current = await wait_for_status(store, user.user_id, commit.run.run_id, "failed")
        assert current.failure.code == code and current.resume_allowed == resumable
        assert "secret" not in current.failure.model_dump_json()
        assert await pool.fetchval("SELECT count(*) FROM reports") == 0
        await worker.tick()
        assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1
    finally:
        await worker.aclose()


async def test_two_runners_cannot_exceed_pg_global_capacity(pg_database):
    pool, store, user = await setup_store(pg_database)
    await ready(store, user.user_id)
    await ready(store, user.user_id)

    async def execute(claimed, stop):
        await asyncio.Event().wait()

    workers = [runner(store, execute, worker_id=str(uuid4())) for _ in range(2)]
    try:
        outcomes = await asyncio.gather(
            *(worker.tick() for worker in workers), return_exceptions=True
        )
        assert not any(isinstance(value, BaseException) for value in outcomes)
        assert await pool.fetchval("SELECT count(*) FROM research_runs WHERE status='running'") == 1
        assert sum(worker.active is not None for worker in workers) == 1
    finally:
        await asyncio.gather(*(worker.aclose() for worker in workers))
