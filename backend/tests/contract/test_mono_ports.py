"""Stage-zero contracts. Fake transactions are not PostgreSQL evidence."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from application.errors import AppError
from application.ports import ResearchRepositoryPort, UnitOfWorkPort, UserRepositoryPort
from application.records import FreezeCommit, SessionChange, User
from application.settings import Settings
from domain.ports import ClockPort
from domain.research.ids import canonical_hash
from domain.research.models import BriefRecord, ResearchRun, SessionState
from domain.research.state import Checkpoint, PipelineState
from infrastructure.fake import FakeClock, FakeResearchDatabase


def session_change(owner):
    now = datetime.now(UTC)
    session = SessionState(
        session_id=uuid4(),
        owner_id=owner,
        query="Controlled fixture question",
        status="ask",
        revision=1,
        brief_draft={},
        brief_version=1,
        pending_questions=["Which scope?"],
        missing_fields=["scope"],
        clarification_round=0,
        clarification_limit_reached=False,
        source_selection={},
        run_id=None,
        failure=None,
        created_at=now,
        updated_at=now,
    )
    brief = BriefRecord(
        session_id=session.session_id,
        version=1,
        content={},
        frozen_at=None,
        confirmed_by=None,
        content_hash=None,
        source_selection={},
    )
    return SessionChange(session=session, messages=[], brief=brief)


async def create_owner(db):
    user = User(
        user_id=uuid4(),
        email=" Fixture@Example.org ",
        password_hash="fixture-hash",
        is_development=False,
        created_at=datetime.now(UTC),
    )
    async with db.transaction() as tx:
        await db.users.create(user, tx)
    return user


@pytest.mark.asyncio
async def test_multiple_repositories_share_atomic_transaction():
    db = FakeResearchDatabase()
    uow: UnitOfWorkPort = db
    users: UserRepositoryPort = db.users
    repo: ResearchRepositoryPort = db.research
    user = User(
        user_id=uuid4(),
        email="fixture@example.org",
        password_hash="fixture-hash",
        is_development=False,
        created_at=datetime.now(UTC),
    )
    change = session_change(user.user_id)
    with pytest.raises(RuntimeError, match="injected"):
        async with uow.transaction() as tx:
            await users.create(user, tx)
            await repo.commit_session_change(0, change, tx)
            raise RuntimeError("injected rollback")
    assert await users.get_by_id(user.user_id) is None
    assert await repo.get_session(user.user_id, change.session.session_id) is None
    async with uow.transaction() as tx:
        await users.create(user, tx)
        await repo.commit_session_change(0, change, tx)
    assert (await users.get_by_email("fixture@example.org")).user_id == user.user_id
    assert await repo.get_session(user.user_id, change.session.session_id) == change.session
    assert await repo.get_session(uuid4(), change.session.session_id) is None


@pytest.mark.asyncio
async def test_revision_compare_and_swap_rejects_late_candidate():
    db = FakeResearchDatabase()
    owner = await create_owner(db)
    change = session_change(owner.user_id)
    async with db.transaction() as tx:
        await db.research.commit_session_change(0, change, tx)
    async with db.transaction() as tx:
        with pytest.raises(AppError, match="stale_resource"):
            await db.research.commit_session_change(0, change, tx)
    assert (await db.research.get_session(owner.user_id, change.session.session_id)).revision == 1


@pytest.mark.asyncio
async def test_foreign_and_closed_transaction_handles_are_rejected():
    db, other = FakeResearchDatabase(), FakeResearchDatabase()
    owner = await create_owner(db)
    async with db.transaction() as tx:
        with pytest.raises(ValueError, match="foreign"):
            await other.research.commit_session_change(0, session_change(owner.user_id), tx)
    with pytest.raises(ValueError, match="closed"):
        await db.research.commit_session_change(0, session_change(owner.user_id), tx)


def test_clock_has_independent_wall_and_monotonic_domains():
    clock = FakeClock(datetime(2026, 10, 5, tzinfo=UTC))
    port: ClockPort = clock
    clock.advance(20)
    assert port.monotonic() == 20
    assert clock.now_utc().second == 20
    clock.shift_wall(-3600)
    assert clock.monotonic() == 20


def assessed_change(owner):
    change = session_change(owner)
    brief = {
        "task_type": "evaluation_design",
        "assumptions": "",
        **{
            key: "Controlled fixture boundary"
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
    session = SessionState.model_validate(
        change.session.model_dump()
        | {"status": "confirm", "brief_draft": brief, "pending_questions": [], "missing_fields": []}
    )
    record = BriefRecord.model_validate(change.brief.model_dump() | {"content": brief})
    return SessionChange(session=session, messages=[], brief=record)


def freeze_commit(change):
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


async def test_fake_run_leases_follow_capacity_time_and_latest_seq_contract():
    db = FakeResearchDatabase()
    owner = await create_owner(db)
    change = assessed_change(owner.user_id)
    commit = freeze_commit(change)
    async with db.transaction() as tx:
        await db.research.commit_session_change(0, change, tx)
        await db.research.freeze_and_create_run(commit, tx)
    async with db.transaction() as tx:
        claimed = await db.research.claim_run("controlled-worker", tx)
    assert claimed.run.lease_token == 1 and claimed.owner_id == owner.user_id
    snapshot = await db.research.load_latest_checkpoint(owner.user_id, claimed.run.run_id)
    assert snapshot.seq == 1
    point = Checkpoint.model_validate(snapshot.model_dump() | {"seq": 2, "snapshot_id": uuid4()})
    async with db.transaction() as tx:
        claimed = await db.research.commit_checkpoint(claimed, 1, point, tx)
    assert claimed.run.checkpoint_seq == 2
    assert (await db.research.load_latest_checkpoint(owner.user_id, claimed.run.run_id)).seq == 2
    db.clock.advance(20)
    async with db.transaction() as tx:
        renewed = await db.research.renew_lease(claimed, tx)
    assert renewed.run.lease_expires_at > claimed.run.lease_expires_at
    db.clock.advance(91)
    with pytest.raises(AppError, match="stale_resource"):
        async with db.transaction() as tx:
            await db.research.renew_lease(renewed, tx)
    async with db.transaction() as tx:
        recovered = await db.research.scan_interrupted(tx)
    assert recovered[0].status == "failed" and recovered[0].failure.code == "interrupted"
    async with db.transaction() as tx:
        resumed = await db.research.resume_run(
            owner.user_id, commit.session.session_id, 2, commit.run.config_snapshot, tx
        )
    assert resumed.run_id == commit.run.run_id and resumed.checkpoint_seq == 2
    async with db.transaction() as tx:
        claimed = await db.research.claim_run("next-worker", tx)
        assert claimed.run.lease_token == 2 and claimed.run.attempt_count == 2
        cancelled = await db.research.request_cancel(owner.user_id, commit.session.session_id, tx)
        assert cancelled.status == "cancelling"
        stopped = await db.research.finish_cancelled(claimed, tx)
        assert stopped.status == "cancelled" and stopped.lease_owner is None


@pytest.mark.asyncio
async def test_freeze_and_cached_success_share_one_transaction_and_rollback():
    db = FakeResearchDatabase()
    owner = await create_owner(db)
    change = assessed_change(owner.user_id)
    commit = freeze_commit(change)
    async with db.transaction() as tx:
        await db.research.commit_session_change(0, change, tx)
        reservation = await db.requests.reserve(
            owner.user_id,
            "confirm:" + str(change.session.session_id),
            "fixture-key",
            canonical_hash({"accepted": True}),
            tx,
        )
    with pytest.raises(RuntimeError, match="injected"):
        async with db.transaction() as tx:
            await db.research.freeze_and_create_run(commit, tx)
            await db.requests.complete(
                reservation, 202, {"status": "ready"}, tx, resource_id=commit.run.run_id
            )
            raise RuntimeError("injected before commit")
    assert await db.research.get_run(owner.user_id, commit.run.run_id) is None
    assert (
        await db.research.load_brief(owner.user_id, change.session.session_id, 1)
    ).frozen_at is None
    assert (
        await db.research.get_session(owner.user_id, change.session.session_id)
    ).status == "confirm"
    async with db.transaction() as tx:
        await db.research.freeze_and_create_run(commit, tx)
        await db.requests.complete(
            reservation, 202, {"status": "ready"}, tx, resource_id=commit.run.run_id
        )
    assert (
        await db.research.load_checkpoint(owner.user_id, commit.run.run_id, 1) == commit.checkpoint
    )
    assert await db.research.load_checkpoint(uuid4(), commit.run.run_id, 1) is None
    async with db.transaction() as tx:
        replay = await db.requests.reserve(
            owner.user_id, reservation.operation, reservation.key, reservation.request_hash, tx
        )
    assert replay.state == "completed"
    assert replay.response_status == 202
    assert replay.resource_id == commit.run.run_id


@pytest.mark.asyncio
async def test_idempotency_conflicts_expiry_and_stale_holder():
    db = FakeResearchDatabase()
    owner = await create_owner(db)
    request_hash = canonical_hash({"query": "controlled"})
    async with db.transaction() as tx:
        first = await db.requests.reserve(owner.user_id, "start", "same-key", request_hash, tx)
    async with db.transaction() as tx:
        with pytest.raises(AppError, match="idempotency_conflict"):
            await db.requests.reserve(
                owner.user_id, "start", "same-key", canonical_hash("different"), tx
            )
        with pytest.raises(AppError, match="request_in_progress"):
            await db.requests.reserve(owner.user_id, "start", "same-key", request_hash, tx)
    db.clock.advance(121)
    async with db.transaction() as tx:
        replacement = await db.requests.reserve(
            owner.user_id, "start", "same-key", request_hash, tx
        )
        with pytest.raises(AppError, match="request_in_progress"):
            await db.requests.complete(first, 201, {}, tx)
        await db.requests.release(replacement, tx)
    async with db.transaction() as tx:
        third = await db.requests.reserve(owner.user_id, "start", "same-key", request_hash, tx)
        await db.requests.complete(third, 201, {"session_id": "fixture"}, tx)


@pytest.mark.asyncio
async def test_concurrent_candidate_commits_have_one_cas_winner():
    db = FakeResearchDatabase()
    owner = await create_owner(db)
    change = session_change(owner.user_id)
    async with db.transaction() as tx:
        await db.research.commit_session_change(0, change, tx)
    session = SessionState.model_validate(
        change.session.model_dump() | {"revision": 2, "brief_version": 2}
    )
    brief = BriefRecord.model_validate(change.brief.model_dump() | {"version": 2})
    candidate = SessionChange(session=session, brief=brief, messages=[])

    async def commit_candidate():
        async with db.transaction() as tx:
            await db.research.commit_session_change(1, candidate, tx)

    results = await asyncio.gather(commit_candidate(), commit_candidate(), return_exceptions=True)
    assert sum(result is None for result in results) == 1
    assert (
        sum(isinstance(result, AppError) and result.code == "stale_resource" for result in results)
        == 1
    )


def test_freeze_aggregate_cannot_mix_resource_identity_or_started_run():
    commit = freeze_commit(assessed_change(uuid4()))
    data = commit.model_dump(mode="json")
    for change in (
        {"session": data["session"] | {"revision": 4}},
        {"run": data["run"] | {"session_id": str(uuid4())}},
        {"run": data["run"] | {"attempt_count": 1}},
        {"brief": data["brief"] | {"confirmed_by": str(uuid4())}},
        {"checkpoint": data["checkpoint"] | {"seq": 2}},
    ):
        with pytest.raises(ValidationError):
            FreezeCommit.model_validate(data | change)
