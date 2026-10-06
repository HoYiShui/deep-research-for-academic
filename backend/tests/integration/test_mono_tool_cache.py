"""Real PG attempt accounting; service + real MinIO cases follow below."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from application.errors import AppError
from application.tool_budget import ToolBudgetRequest
from application.tool_calls import ToolCallService, ToolOutput
from domain.content import ContentRef
from domain.ports import AdapterError
from domain.research.ids import canonical_hash
from domain.research.models import Failure
from domain.research.state import Checkpoint, PipelineState
from domain.research.tool_calls import ToolCallIdentity
from infrastructure.clock import SystemClock
from infrastructure.storage.research_postgres import PostgresResearchStore
from tests.integration.test_mono_run_lifecycle import claim, ready, resume
from tests.integration.test_mono_transactions import setup_store


def identity(claimed, index=1, tool="search"):
    return ToolCallIdentity(
        run_id=claimed.run.run_id,
        tool=tool,
        provider="controlled-provider",
        version="fixture-v1",
        arguments={"query": f"Public fixture {index}"},
        input_hash="a" * 64,
        source_policy=claimed.run.config_snapshot.source_policy,
        knowledge_snapshot=[],
    )


async def reserve(store, claimed, value, *, tokens=0, terminal=False, replay=False, elapsed=0):
    async with store.transaction() as tx:
        return await store.research.reserve_tool_call(
            claimed,
            value,
            ToolBudgetRequest(tool=value.tool, token_reservation=tokens, terminal=terminal),
            tx,
            allow_uncertain_replay=replay,
            elapsed_s=elapsed,
        )


def reference(claimed):
    return ContentRef(
        key=f"tool-results/{claimed.run.run_id}/" + "b" * 64,
        sha256="b" * 64,
        size=2,
        media_type="application/json",
    )


async def succeed(store, claimed, receipt, tokens=0):
    async with store.transaction() as tx:
        return await store.research.finish_tool_call(
            claimed,
            receipt,
            tx,
            reference=reference(claimed),
            tokens_used=tokens,
        )


async def started(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    claimed = await claim(store, str(uuid4()))
    return pool, store, user, commit, claimed


async def test_real_concurrent_search_reservations_never_exceed_frozen_limit(pg_database):
    pool, store, user, commit, claimed = await started(pg_database)
    values = await asyncio.gather(
        *(reserve(store, claimed, identity(claimed, index)) for index in range(80)),
        return_exceptions=True,
    )
    successes = [value for value in values if not isinstance(value, Exception)]
    failures = [value for value in values if isinstance(value, Exception)]
    assert len(successes) == 60 and len(failures) == 20
    assert all(
        isinstance(value, AppError) and value.code == "budget_exhausted" for value in failures
    )
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 60
    budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
    assert budget.used.search_calls == 0 and budget.pending.search_calls == 60


async def test_same_call_concurrency_only_reserves_one_attempt_and_success_hits_cache(pg_database):
    pool, store, _user, commit, claimed = await started(pg_database)
    value = identity(claimed)
    receipts = await asyncio.gather(
        *(reserve(store, claimed, value) for _ in range(6)), return_exceptions=True
    )
    executed = [r for r in receipts if not isinstance(r, Exception)]
    assert len(executed) == 1
    assert all(r.code == "tool_call_in_progress" for r in receipts if isinstance(r, AppError))
    finished = await succeed(store, claimed, executed[0])
    cached = await reserve(store, claimed, value)
    assert cached.disposition == "cache" and cached.reference == finished.reference
    assert cached.budget.used.search_calls == 1 and cached.budget.pending.search_calls == 0
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
    with pytest.raises(AppError, match="session_not_found"):
        await store.research.load_tool_budget(uuid4(), commit.run.run_id)


async def test_model_token_holds_settle_to_measured_usage_and_preserve_terminal_reserve(
    pg_database,
):
    _pool, store, _user, _commit, claimed = await started(pg_database)
    receipt = await reserve(store, claimed, identity(claimed, tool="llm"), tokens=100000)
    with pytest.raises(AppError, match="budget_exhausted"):
        await reserve(store, claimed, identity(claimed, 2, "llm"), tokens=9000)
    finished = await succeed(store, claimed, receipt, tokens=10000)
    assert finished.budget.used.tokens == 10000 and finished.budget.pending.tokens == 0
    second = await reserve(store, claimed, identity(claimed, 2, "llm"), tokens=98000)
    with pytest.raises(AppError, match="budget_exhausted"):
        await reserve(store, claimed, identity(claimed, 3, "llm"), tokens=1)
    terminal = await reserve(
        store, claimed, identity(claimed, 3, "llm"), tokens=12000, terminal=True
    )
    assert terminal.budget.pending.tokens == 110000
    assert second.budget.pending.tokens == 98000


async def test_unknown_attempt_resume_requires_explicit_readonly_replay_and_keeps_charges(
    pg_database,
):
    pool, store, user, commit, old = await started(pg_database)
    value = identity(old, tool="llm")
    original = await reserve(store, old, value, tokens=1000, elapsed=12)
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, 1)
    newer = await claim(store, str(uuid4()))
    uncertain = await reserve(store, newer, value, tokens=1000)
    assert uncertain.disposition == "uncertain"
    assert uncertain.budget.used.llm_calls == 1 and uncertain.budget.used.tokens == 1000
    assert uncertain.budget.used.elapsed_s == 12
    replayed = await reserve(store, newer, value, tokens=1000, replay=True)
    assert replayed.attempt == 2 and replayed.uncertain_replay
    final = await succeed(store, newer, replayed, tokens=50)
    assert final.budget.used.llm_calls == 2 and final.budget.used.tokens == 1050
    assert (
        await pool.fetchval("SELECT count(*) FROM tool_call_attempts WHERE status='uncertain'") == 1
    )
    with pytest.raises(AppError, match="stale_resource"):
        await succeed(store, old, original, tokens=50)


async def test_cancel_stops_new_reservations_but_completed_io_can_settle(pg_database):
    pool, store, user, commit, claimed = await started(pg_database)
    receipt = await reserve(store, claimed, identity(claimed))
    async with store.transaction() as tx:
        await store.research.request_cancel(user.user_id, commit.session.session_id, tx)
    with pytest.raises(AppError, match="invalid_session_state"):
        await reserve(store, claimed, identity(claimed, 2))
    finished = await succeed(store, claimed, receipt)
    assert finished.record.status == "succeeded"
    assert await pool.fetchval("SELECT status FROM research_runs") == "cancelling"


async def test_reservation_and_baseline_roll_back_on_sql_fault(pg_database):
    pool, store, _user, _commit, claimed = await started(pg_database)
    await pool.execute("""
        CREATE FUNCTION injected_tool_fault() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'fixture fault'; END; $$;
        CREATE TRIGGER injected_fault BEFORE INSERT ON tool_call_attempts
        FOR EACH ROW EXECUTE FUNCTION injected_tool_fault();
    """)

    with pytest.raises(AdapterError):
        await reserve(store, claimed, identity(claimed))
    for table in ("tool_calls", "tool_call_attempts", "tool_budget_baselines"):
        assert await pool.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_snapshot_must_match_settled_ledger_not_reset_or_double_count(pg_database):
    pool, store, user, commit, claimed = await started(pg_database)
    receipt = await reserve(store, claimed, identity(claimed, tool="llm"), tokens=1000)
    finished = await succeed(store, claimed, receipt, tokens=73)
    point = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    for calls, tokens in [(0, 0), (2, 146)]:
        data = point.state.model_dump()
        data["run_metadata"]["budget_used"].update(llm_calls=calls, tokens=tokens)
        state = PipelineState.model_validate(data)
        candidate = Checkpoint(
            snapshot_id=uuid4(),
            run_id=state.run_id,
            seq=2,
            schema_version=1,
            phase=state.phase,
            state=state,
            state_hash=canonical_hash(state),
            created_at=datetime.now(UTC),
        )
        with pytest.raises(AppError, match="invalid_state"):
            async with store.transaction() as tx:
                await store.research.commit_checkpoint(claimed, 1, candidate, tx)
    data = point.state.model_dump()
    data["run_metadata"]["budget_used"] = finished.budget.used.model_dump()
    state = PipelineState.model_validate(data)
    candidate = Checkpoint(
        snapshot_id=uuid4(),
        run_id=state.run_id,
        seq=2,
        schema_version=1,
        phase=state.phase,
        state=state,
        state_hash=canonical_hash(state),
        created_at=datetime.now(UTC),
    )
    async with store.transaction() as tx:
        newer = await store.research.commit_checkpoint(claimed, 1, candidate, tx)
    next_call = await reserve(store, newer, identity(newer, 2, "llm"), tokens=1000)
    assert next_call.budget.used.llm_calls == 1 and next_call.budget.pending.llm_calls == 1
    assert await pool.fetchval("SELECT checkpoint_seq FROM tool_budget_baselines") == 1


def service(store, claimed, cache):
    return ToolCallService(claimed, store, store.research, cache, SystemClock(), elapsed_base=0)


async def test_real_pg_minio_success_cache_survives_new_service_and_never_reissues(
    pg_database, object_cache
):
    pool, store, user, commit, claimed = await started(pg_database)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        # Actual external I/O callback occurs after the PG reservation commit.
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND state='idle in transaction'"
            )
            == 0
        )
        return ToolOutput(content=[], tokens_used=0)

    value = identity(claimed)
    request = ToolBudgetRequest(tool="search", token_reservation=0, terminal=False)
    first = service(store, claimed, object_cache)
    assert (await first.invoke(value, request, operation)).content == []
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, 1)
    fresh_store = PostgresResearchStore(pool)
    newer = await claim(fresh_store, str(uuid4()))
    assert newer.run.run_id == claimed.run.run_id and newer.run.lease_token == 2
    fresh = service(fresh_store, newer, object_cache)
    assert (await fresh.invoke(value, request, operation)).content == []
    assert calls == 1
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
    row = await pool.fetchrow(
        "SELECT status,result_object_key,result_hash,result_size FROM tool_calls"
    )
    assert row["status"] == "succeeded" and row["result_size"] > 0
    assert row["result_object_key"].endswith(row["result_hash"])


async def test_committed_missing_result_never_silently_repeats_paid_call(pg_database, object_cache):
    pool, store, _user, _commit, claimed = await started(pg_database)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return ToolOutput(content="Measured model response", tokens_used=73)

    value = identity(claimed, tool="llm")
    request = ToolBudgetRequest(tool="llm", token_reservation=100, terminal=False)
    runner = service(store, claimed, object_cache)
    await runner.invoke(value, request, operation)
    key = await pool.fetchval("SELECT result_object_key FROM tool_calls")
    await asyncio.to_thread(object_cache._client.remove_object, object_cache.bucket, key)
    with pytest.raises(AdapterError) as missing:
        await runner.invoke(value, request, operation)
    assert missing.value.code == "content_missing" and calls == 1
    assert await pool.fetchval("SELECT status FROM tool_calls") == "succeeded"


async def test_real_uncertain_failure_is_sanitized_and_explicit_replay_is_charged(
    pg_database, object_cache
):
    pool, store, _user, _commit, claimed = await started(pg_database)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("DO-NOT-PERSIST-SECRET")
        return ToolOutput(content={"text": "Public response"}, tokens_used=73)

    value = identity(claimed, tool="llm")
    request = ToolBudgetRequest(tool="llm", token_reservation=100, terminal=False)
    runner = service(store, claimed, object_cache)
    with pytest.raises(AdapterError) as failed:
        await runner.invoke(value, request, operation)
    assert "SECRET" not in str(failed.value)
    assert "SECRET" not in await pool.fetchval("SELECT failure::text FROM tool_calls")
    with pytest.raises(AppError, match="tool_call_uncertain"):
        await runner.invoke(value, request, operation)
    assert calls == 1
    await runner.invoke(value, request, operation, allow_uncertain_replay=True)
    assert calls == 2
    assert (
        await pool.fetchval("SELECT count(*) FROM tool_call_attempts WHERE uncertain_replay") == 1
    )
    budget = await store.research.load_tool_budget(claimed.owner_id, claimed.run.run_id)
    assert budget.used.llm_calls == 2 and budget.used.tokens == 173


async def test_cancelled_operation_persists_uncertainty_without_orphan_task(
    pg_database, object_cache
):
    pool, store, _user, _commit, claimed = await started(pg_database)
    entered = asyncio.Event()

    async def operation():
        entered.set()
        await asyncio.Event().wait()

    value = identity(claimed, tool="llm")
    request = ToolBudgetRequest(tool="llm", token_reservation=100, terminal=False)
    runner = service(store, claimed, object_cache)
    task = asyncio.create_task(runner.invoke(value, request, operation))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runner.close()
    assert not runner._cleanup
    assert await pool.fetchval("SELECT status FROM tool_calls") == "uncertain"
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1


async def test_measured_overrun_is_retained_and_blocks_further_calls(pg_database, object_cache):
    pool, store, _user, _commit, claimed = await started(pg_database)

    async def operation():
        return ToolOutput(content="Provider violated the reserved bound", tokens_used=101)

    value = identity(claimed, tool="llm")
    request = ToolBudgetRequest(tool="llm", token_reservation=100, terminal=False)
    runner = service(store, claimed, object_cache)
    with pytest.raises(AppError, match="budget_reservation_exceeded"):
        await runner.invoke(value, request, operation)
    assert await pool.fetchval("SELECT tokens_used FROM tool_call_attempts") == 101
    with pytest.raises(AppError, match="budget_reservation_exceeded"):
        await runner.invoke(identity(claimed, 2, "llm"), request, operation)
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1


async def test_expiring_lease_rolls_back_all_reservation_writes(pg_database):
    pool, store, _user, _commit, claimed = await started(pg_database)
    await pool.execute("""
        CREATE FUNCTION slow_tool_reservation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_sleep(0.2); RETURN NEW; END; $$;
        CREATE TRIGGER slow_reservation BEFORE INSERT ON tool_call_attempts
        FOR EACH ROW EXECUTE FUNCTION slow_tool_reservation();
        UPDATE research_runs SET lease_expires_at=clock_timestamp()+interval '0.1 seconds';
    """)
    with pytest.raises(AppError, match="stale_resource"):
        await reserve(store, claimed, identity(claimed))
    for table in ("tool_calls", "tool_call_attempts", "tool_budget_baselines"):
        assert await pool.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_nonreadonly_uncertainty_cannot_be_replayed(pg_database):
    pool, store, user, commit, claimed = await started(pg_database)
    value = identity(claimed, tool="analysis")
    await reserve(store, claimed, value)
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, 1)
    newer = await claim(store, str(uuid4()))
    assert (await reserve(store, newer, value)).disposition == "uncertain"
    with pytest.raises(AppError, match="invalid_state"):
        await reserve(store, newer, value, replay=True)
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1


async def test_known_failed_usage_is_not_refunded_or_hidden(pg_database):
    pool, store, _user, _commit, claimed = await started(pg_database)
    receipt = await reserve(store, claimed, identity(claimed, tool="llm"), tokens=100)
    failure = Failure(
        code="model_output_invalid",
        dependency="llm",
        operation="plan",
        phase="plan",
        message="Invalid model output",
        retryable=False,
        resume_allowed=True,
        attempt=1,
        occurred_at=datetime.now(UTC),
        details=None,
    )
    async with store.transaction() as tx:
        settled = await store.research.finish_tool_call(
            claimed, receipt, tx, failure=failure, tokens_used=73
        )
    assert settled.record.status == "failed"
    assert settled.budget.used.llm_calls == 1 and settled.budget.used.tokens == 73
    assert settled.budget.pending.llm_calls == 0 and settled.budget.pending.tokens == 0
    assert await pool.fetchval("SELECT tokens_used FROM tool_call_attempts") == 73


async def test_new_lease_reclassifies_all_unfinished_calls_before_any_replay(pg_database):
    pool, store, user, commit, claimed = await started(pg_database)
    first = await reserve(store, claimed, identity(claimed, 1))
    await succeed(store, claimed, first)
    await reserve(store, claimed, identity(claimed, 2))
    await reserve(store, claimed, identity(claimed, 3, "llm"), tokens=100)
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, 1)
    newer = await claim(store, str(uuid4()))
    assert newer.run.lease_token == 2
    assert await pool.fetchval("SELECT count(*) FROM tool_calls WHERE status='uncertain'") == 2
    budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
    assert budget.used.search_calls == 2 and budget.used.llm_calls == 1
    assert budget.used.tokens == 100
    assert budget.pending.search_calls == 0 and budget.pending.llm_calls == 0
