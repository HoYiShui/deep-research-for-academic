"""Real PG tests of shared repositories, not direct Service/HTTP substitutes."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from application.errors import AppError
from application.records import FreezeCommit, SessionChange, User
from application.settings import Settings
from domain.ports import AdapterError
from domain.research.ids import canonical_hash
from domain.research.models import BriefRecord, Message, ResearchRun, SessionState
from domain.research.state import Checkpoint, PipelineState
from infrastructure.storage import research_postgres
from infrastructure.storage.migrations import run_migrations
from infrastructure.storage.research_postgres import PostgresResearchStore


def candidate(owner, task="evaluation_design"):
    now = datetime.now(UTC)
    brief = {
        "task_type": task,
        "assumptions": "",
        **{
            key: "Synthetic explicit boundary"
            for key in (
                "decision_goal",
                "research_object",
                "scope",
                "comparison_scope",
                "claims_to_verify",
                "evidence_requirements",
                "conclusion_boundary",
                "deliverable",
            )
        },
    }
    session = SessionState(
        session_id=uuid4(),
        owner_id=owner,
        query="Synthetic question",
        status="confirm",
        revision=1,
        brief_draft=brief,
        brief_version=1,
        pending_questions=[],
        missing_fields=[],
        clarification_round=0,
        clarification_limit_reached=False,
        source_selection={},
        run_id=None,
        failure=None,
        created_at=now,
        updated_at=now,
    )
    record = BriefRecord(
        session_id=session.session_id,
        version=1,
        content=brief,
        source_selection={},
        frozen_at=None,
        confirmed_by=None,
        content_hash=None,
    )
    return SessionChange(session=session, brief=record, messages=[])


def freezing(change):
    now, run_id = datetime.now(UTC), uuid4()
    session = SessionState.model_validate(
        change.session.model_dump() | {"status": "ready", "revision": 2, "run_id": run_id}
    )
    brief = BriefRecord.model_validate(
        change.brief.model_dump()
        | {
            "frozen_at": now,
            "confirmed_by": session.owner_id,
            "content_hash": canonical_hash(change.brief.content),
        }
    )
    config = Settings().run_config_snapshot()
    state = PipelineState.initial(
        session_id=session.session_id,
        run_id=run_id,
        brief_version=1,
        research_brief=brief.content,
        source_selection=session.source_selection,
        config=config,
    )
    run = ResearchRun(
        run_id=run_id,
        session_id=session.session_id,
        brief_version=1,
        brief_hash=brief.content_hash,
        status="ready",
        phase="plan",
        attempt_count=0,
        checkpoint_seq=1,
        cancel_requested_at=None,
        lease_owner=None,
        lease_token=0,
        lease_expires_at=None,
        resume_allowed=False,
        failure=None,
        config_snapshot=config,
        created_at=now,
        started_at=None,
        finished_at=None,
    )
    checkpoint = Checkpoint(
        snapshot_id=uuid4(),
        run_id=run_id,
        seq=1,
        schema_version=1,
        phase="plan",
        state=state,
        state_hash=canonical_hash(state),
        created_at=now,
    )
    return FreezeCommit(
        expected_revision=1,
        session=session,
        brief=brief,
        run=run,
        checkpoint=checkpoint,
        messages=[],
    )


async def setup_store(pg_database):
    pool, _ = pg_database
    await run_migrations(pool)
    store = PostgresResearchStore(pool)
    owner = User(
        user_id=uuid4(),
        email="fixture@example.org",
        password_hash="fixture-hash",
        is_development=False,
        created_at=datetime.now(UTC),
    )
    async with store.transaction() as tx:
        await store.users.create(owner, tx)
    return pool, store, owner


@pytest.mark.asyncio
async def test_shared_transaction_rollback_owner_and_typed_reads(pg_database):
    pool, store, owner = await setup_store(pg_database)
    change = candidate(owner.user_id)
    with pytest.raises(RuntimeError, match="injected"):
        async with store.transaction() as tx:
            await store.research.commit_session_change(0, change, tx)
            await store.requests.reserve(
                owner.user_id, "start", "key", canonical_hash("request"), tx
            )
            raise RuntimeError("injected rollback")
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
    assert await pool.fetchval("SELECT count(*) FROM briefs") == 0
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 0
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
    assert (
        await store.research.get_session(owner.user_id, change.session.session_id) == change.session
    )
    assert await store.research.get_session(uuid4(), change.session.session_id) is None
    assert await store.research.load_brief(uuid4(), change.session.session_id, 1) is None
    assert await store.users.get_by_email("FIXTURE@example.org") == owner


@pytest.mark.asyncio
async def test_freeze_response_and_checkpoint_are_atomic_on_real_pg(pg_database):
    pool, store, owner = await setup_store(pg_database)
    change, digest = candidate(owner.user_id), canonical_hash({"accepted": True})
    commit = freezing(change)
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
        reservation = await store.requests.reserve(owner.user_id, "confirm", "key", digest, tx)
    with pytest.raises(RuntimeError, match="injected"):
        async with store.transaction() as tx:
            await store.research.freeze_and_create_run(commit, tx)
            await store.requests.complete(
                reservation, 202, {"status": "ready"}, tx, resource_id=commit.run.run_id
            )
            raise RuntimeError("injected before commit")
    assert (
        await store.research.get_session(owner.user_id, change.session.session_id)
    ).status == "confirm"
    assert (
        await store.research.load_brief(owner.user_id, change.session.session_id, 1)
    ).frozen_at is None
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 0
    assert await pool.fetchval("SELECT state FROM idempotency_requests") == "in_progress"
    async with store.transaction() as tx:
        await store.research.freeze_and_create_run(commit, tx)
        await store.requests.complete(
            reservation, 202, {"status": "ready"}, tx, resource_id=commit.run.run_id
        )
    assert await store.research.get_run(owner.user_id, commit.run.run_id) == commit.run
    assert (
        await store.research.load_checkpoint(owner.user_id, commit.run.run_id, 1)
        == commit.checkpoint
    )
    assert await store.research.load_checkpoint(uuid4(), commit.run.run_id, 1) is None
    async with store.transaction() as tx:
        replay = await store.requests.reserve(owner.user_id, "confirm", "key", digest, tx)
    assert replay.state == "completed" and replay.response_status == 202
    assert replay.resource_id == commit.run.run_id


@pytest.mark.asyncio
async def test_real_concurrent_revision_and_reservation_competition(pg_database):
    _, store, owner = await setup_store(pg_database)
    change = candidate(owner.user_id)
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
    updated = SessionChange(
        session=SessionState.model_validate(
            change.session.model_dump() | {"revision": 2, "brief_version": 2}
        ),
        brief=BriefRecord.model_validate(change.brief.model_dump() | {"version": 2}),
        messages=[],
    )

    async def update():
        async with store.transaction() as tx:
            await store.research.commit_session_change(1, updated, tx)

    results = await asyncio.gather(update(), update(), return_exceptions=True)
    assert sum(result is None for result in results) == 1
    assert (
        sum(isinstance(result, AppError) and result.code == "stale_resource" for result in results)
        == 1
    )

    async def reserve():
        async with store.transaction() as tx:
            return await store.requests.reserve(
                owner.user_id, "start", "race-key", canonical_hash("request"), tx
            )

    results = await asyncio.gather(reserve(), reserve(), return_exceptions=True)
    assert (
        sum(
            isinstance(result, AppError) and result.code == "request_in_progress"
            for result in results
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_table", ["research_runs", "phase_snapshots"])
async def test_sql_failure_during_freeze_leaves_no_half_frozen_state(
    pg_database, monkeypatch, failure_table
):
    pool, store, owner = await setup_store(pg_database)
    change = candidate(owner.user_id)
    commit = freezing(change)
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
    original = research_postgres.insert

    async def failing_insert(conn, table, data, suffix=""):
        if table == failure_table:
            await conn.execute("SELECT 1/0")
        return await original(conn, table, data, suffix)

    monkeypatch.setattr(research_postgres, "insert", failing_insert)
    with pytest.raises(AdapterError, match="Transaction could not commit"):
        async with store.transaction() as tx:
            await store.research.freeze_and_create_run(commit, tx)
    assert (
        await store.research.load_brief(owner.user_id, change.session.session_id, 1)
    ).frozen_at is None
    assert (
        await store.research.get_session(owner.user_id, change.session.session_id)
    ).status == "confirm"
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 0


@pytest.mark.asyncio
async def test_request_renewal_expiry_late_completion_and_release(pg_database):
    pool, store, owner = await setup_store(pg_database)
    digest = canonical_hash("controlled request")
    async with store.transaction() as tx:
        first = await store.requests.reserve(owner.user_id, "start", "key", digest, tx)
    async with store.transaction() as tx:
        renewed = await store.requests.renew(first, tx)
    assert renewed.lease_expires_at > first.lease_expires_at
    async with store.transaction() as tx:
        with pytest.raises(AppError, match="request_in_progress"):
            await store.requests.complete(first, 201, {}, tx)
    await pool.execute(
        "UPDATE idempotency_requests SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        replacement = await store.requests.reserve(owner.user_id, "start", "key", digest, tx)
        with pytest.raises(AppError, match="request_in_progress"):
            await store.requests.complete(renewed, 201, {}, tx)
        with pytest.raises(ValueError, match="Transient"):
            await store.requests.complete(replacement, 503, {}, tx)
        await store.requests.release(replacement, tx)
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 0
    async with store.transaction() as tx:
        fresh = await store.requests.reserve(owner.user_id, "start", "key", digest, tx)
        await store.requests.complete(fresh, 201, {"status": "confirm"}, tx)
    async with store.transaction() as tx:
        with pytest.raises(AppError, match="idempotency_conflict"):
            await store.requests.reserve(
                owner.user_id, "start", "key", canonical_hash("different"), tx
            )


@pytest.mark.asyncio
async def test_real_transaction_handle_cannot_cross_stores_or_outlive_context(pg_database):
    pool, store, owner = await setup_store(pg_database)
    other = PostgresResearchStore(pool)
    async with store.transaction() as tx:
        with pytest.raises(ValueError, match="foreign"):
            await other.users.get_by_id(owner.user_id, tx)
    with pytest.raises(ValueError, match="closed"):
        await store.users.get_by_id(owner.user_id, tx)


@pytest.mark.asyncio
async def test_two_confirmations_cannot_create_two_runs(pg_database):
    pool, store, owner = await setup_store(pg_database)
    change = candidate(owner.user_id)
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)

    async def confirm(commit):
        async with store.transaction() as tx:
            return await store.research.freeze_and_create_run(commit, tx)

    results = await asyncio.gather(
        confirm(freezing(change)), confirm(freezing(change)), return_exceptions=True
    )
    assert sum(isinstance(result, ResearchRun) for result in results) == 1
    assert (
        sum(isinstance(result, AppError) and result.code == "stale_resource" for result in results)
        == 1
    )
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1


@pytest.mark.asyncio
async def test_cancelled_transaction_rolls_back_freeze(pg_database):
    pool, store, owner = await setup_store(pg_database)
    change = candidate(owner.user_id)
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
    frozen, hold = asyncio.Event(), asyncio.Event()

    async def interrupted():
        async with store.transaction() as tx:
            await store.research.freeze_and_create_run(freezing(change), tx)
            frozen.set()
            await hold.wait()

    task = asyncio.create_task(interrupted())
    try:
        await asyncio.wait_for(frozen.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (
        await store.research.get_session(owner.user_id, change.session.session_id)
    ).status == "confirm"
    assert (
        await store.research.load_brief(owner.user_id, change.session.session_id, 1)
    ).frozen_at is None
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 0


@pytest.mark.asyncio
async def test_append_only_typed_messages_and_immutable_query(pg_database):
    _, store, owner = await setup_store(pg_database)
    change = candidate(owner.user_id)
    message = Message(
        message_id=uuid4(),
        session_id=change.session.session_id,
        sequence=1,
        role="user",
        kind="initial",
        content="Synthetic query",
        assessment=None,
        brief_version=1,
        created_at=change.session.created_at,
    )
    change = SessionChange(session=change.session, brief=change.brief, messages=[message])
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
    assert await store.research.list_messages(owner.user_id, change.session.session_id) == [message]
    assert await store.research.list_messages(uuid4(), change.session.session_id) == []
    session = SessionState.model_validate(
        change.session.model_dump()
        | {"query": "Not the original query", "revision": 2, "brief_version": 2}
    )
    updated = SessionChange(
        session=session,
        brief=BriefRecord.model_validate(change.brief.model_dump() | {"version": 2}),
        messages=[],
    )
    with pytest.raises(AppError, match="immutable"):
        async with store.transaction() as tx:
            await store.research.commit_session_change(1, updated, tx)
    assert (
        await store.research.get_session(owner.user_id, change.session.session_id)
    ).revision == 1


def test_new_run_rejects_phantom_execution_budget():
    data = freezing(candidate(uuid4())).model_dump(mode="json")
    state = data["checkpoint"]["state"]
    state["run_metadata"]["budget_used"]["llm_calls"] = 1
    data["checkpoint"]["state_hash"] = canonical_hash(state)
    with pytest.raises(ValidationError, match="prior execution"):
        FreezeCommit.model_validate(data)
