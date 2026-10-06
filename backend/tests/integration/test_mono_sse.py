"""Broadcast boundaries and durable PG bootstrap, not historical event replay."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from application.errors import AppError
from application.research_queries import ResearchQueries
from application.run_sse import RunEventBus, RunEventStream
from domain.research.run_events import PhaseFrame, ProgressFrame
from tests.integration.test_mono_report_publication import publish, reviewed
from tests.integration.test_mono_run_lifecycle import ready
from tests.integration.test_mono_transactions import candidate, setup_store


def frame(session, run, seq=1):
    return PhaseFrame(
        event_id=uuid4(),
        session_id=session,
        run_id=run,
        timestamp=datetime.now(UTC),
        checkpoint_seq=seq,
        event="phase",
        phase="plan",
        status="running",
        message="Committed planning phase",
    )


def data(wire):
    return json.loads(next(line[6:] for line in wire.splitlines() if line.startswith("data: ")))


async def test_two_subscribers_get_independent_broadcast_not_competing_consumers():
    bus = RunEventBus(queue_size=2)
    session, run = uuid4(), uuid4()
    first, second = bus.subscribe(session), bus.subscribe(session)
    value = frame(session, run)
    bus.emit(value)
    assert await first.queue.get() == await second.queue.get()
    bus.unsubscribe(first)
    bus.emit(frame(session, run, seq=2))
    assert first.queue.empty() and not second.queue.empty()
    bus.unsubscribe(second)
    assert bus.subscriber_count == 0


async def test_slow_consumer_closes_only_its_own_queue():
    bus = RunEventBus(queue_size=1)
    session, run = uuid4(), uuid4()
    slow, fast = bus.subscribe(session), bus.subscribe(session)
    bus.emit(frame(session, run))
    await fast.queue.get()
    bus.emit(frame(session, run, seq=2))
    assert slow.closed and not fast.closed
    assert (await fast.queue.get()).checkpoint_seq == 2
    bus.unsubscribe(fast)
    assert bus.subscriber_count == 0


async def test_progress_same_seq_is_not_deduplicated_and_has_bounded_payload():
    bus = RunEventBus(queue_size=4)
    session, run = uuid4(), uuid4()
    subscription = bus.subscribe(session)
    values = [
        ProgressFrame(
            event_id=uuid4(),
            session_id=session,
            run_id=run,
            timestamp=datetime.now(UTC),
            checkpoint_seq=1,
            event="progress",
            phase="research",
            section_id="section_1",
            stage="query_started",
            unit_id=f"query-{index}",
            completed_units=0,
            total_units=None,
            message="Query started",
        )
        for index in range(2)
    ]
    for value in values:
        bus.emit(value)
    assert (await subscription.queue.get()).event_id != (await subscription.queue.get()).event_id
    with pytest.raises(ValueError):
        ProgressFrame.model_validate(values[0].model_dump() | {"results": {"unsafe": "x" * 9000}})
    bus.unsubscribe(subscription)


async def test_subscription_registered_before_pg_bootstrap_read(pg_database):
    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    bus = RunEventBus()
    real_queries = ResearchQueries(store, store.research)

    class RacingQueries:
        async def session_view(self, owner, session_id):
            assert bus.subscriber_count == 1
            value = frame(session_id, commit.run.run_id)
            bus.emit(PhaseFrame.model_validate(value.model_dump() | {"status": "ready"}))
            return await real_queries.session_view(owner, session_id)

    service = RunEventStream(RacingQueries(), bus, poll_s=0.02, heartbeat_s=0.02)
    stream = await service.open(user.user_id, commit.session.session_id)
    try:
        initial = await anext(stream)
        assert initial.startswith("id: ") and "event: phase" in initial
        assert data(initial)["status"] == "ready"
        assert "Committed planning phase" in await anext(stream)
    finally:
        await stream.aclose()
    assert bus.subscriber_count == 0


async def test_late_completed_subscriber_gets_done_and_read_does_not_execute(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit, claimed, point, report = await reviewed(store, user.user_id)
    await publish(store, claimed, point)
    bus = RunEventBus()
    service = RunEventStream(ResearchQueries(store, store.research), bus)
    stream = await service.open(user.user_id, commit.session.session_id)
    result = await anext(stream)
    assert "event: done" in result
    assert (
        data(result)["status"] == "completed"
        and data(result)["review_verdict"] == report.review_verdict
    )
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert bus.subscriber_count == 0
    assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1


async def test_foreign_and_prefrozen_stream_fail_before_headers_and_remove_subscription(
    pg_database,
):
    _pool, store, user = await setup_store(pg_database)
    change = candidate(user.user_id)
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
    bus = RunEventBus()
    service = RunEventStream(ResearchQueries(store, store.research), bus)
    with pytest.raises(AppError, match="session_not_found"):
        await service.open(uuid4(), change.session.session_id)
    with pytest.raises(AppError, match="invalid_session_state"):
        await service.open(user.user_id, change.session.session_id)
    assert bus.subscriber_count == 0


async def test_poll_recovers_failed_terminal_without_fake_completed_or_restart(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    bus = RunEventBus()
    service = RunEventStream(
        ResearchQueries(store, store.research), bus, poll_s=0.02, heartbeat_s=1
    )
    stream = await service.open(user.user_id, commit.session.session_id)
    assert data(await anext(stream))["status"] == "ready"
    async with store.transaction() as tx:
        await store.research.claim_run("other-process", tx)
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    terminal = await asyncio.wait_for(anext(stream), timeout=2)
    assert data(terminal)["status"] == "failed" and data(terminal)["final_report_url"] is None
    assert data(terminal)["failure"]["code"] == "interrupted"
    await stream.aclose()
    assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1


async def test_pg_unavailable_sends_diagnostic_only_not_invented_done(pg_database):
    from domain.ports import AdapterError

    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    real_queries = ResearchQueries(store, store.research)

    class FailingQueries:
        calls = 0

        async def session_view(self, owner, session_id):
            self.calls += 1
            if self.calls > 1:
                raise AdapterError("postgres", "dependency_unavailable", "safe", True, "read")
            return await real_queries.session_view(owner, session_id)

    bus = RunEventBus()
    service = RunEventStream(FailingQueries(), bus, poll_s=0.02, heartbeat_s=1)
    stream = await service.open(user.user_id, commit.session.session_id)
    await anext(stream)
    error = await asyncio.wait_for(anext(stream), timeout=2)
    assert "event: error" in error and data(error)["fatal"]
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert bus.subscriber_count == 0


async def test_asgi_disconnect_during_send_unsubscribes_without_cancelling_run(pg_database):
    from starlette.requests import ClientDisconnect

    from interface.router.research import _RunStreamingResponse

    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    bus = RunEventBus()
    service = RunEventStream(ResearchQueries(store, store.research), bus)
    stream = await service.open(user.user_id, commit.session.session_id)
    response = _RunStreamingResponse(stream)

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("disconnected")

    async def receive():
        await asyncio.Event().wait()

    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert bus.subscriber_count == 0
    assert (await store.research.get_run(user.user_id, commit.run.run_id)).status == "ready"


async def test_stream_heartbeat_and_close_before_iteration_cleanup(pg_database):
    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    bus = RunEventBus()
    service = RunEventStream(
        ResearchQueries(store, store.research), bus, poll_s=1, heartbeat_s=0.02
    )
    unopened = await service.open(user.user_id, commit.session.session_id)
    await unopened.aclose()
    assert bus.subscriber_count == 0
    stream = await service.open(user.user_id, commit.session.session_id)
    await anext(stream)
    assert await asyncio.wait_for(anext(stream), timeout=1) == ": heartbeat\n\n"
    await stream.aclose()
    assert bus.subscriber_count == 0


async def test_stale_same_seq_status_does_not_regress_bootstrap(pg_database):
    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    bus = RunEventBus()
    service = RunEventStream(
        ResearchQueries(store, store.research), bus, poll_s=1, heartbeat_s=0.02
    )
    stream = await service.open(user.user_id, commit.session.session_id)
    bus.emit(
        frame(commit.session.session_id, commit.run.run_id)
    )  # deliberately invalid stale status
    assert data(await anext(stream))["status"] == "ready"
    assert await asyncio.wait_for(anext(stream), timeout=1) == ": heartbeat\n\n"
    await stream.aclose()
