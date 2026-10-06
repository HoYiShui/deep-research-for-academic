"""Real PG lease/capacity fencing; later cases add checkpoints/termination/SSE."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from application.errors import AppError
from application.records import User
from domain.ports import AdapterError
from domain.research.ids import canonical_hash
from domain.research.state import Checkpoint, PipelineState
from infrastructure.storage import research_postgres
from tests.integration.test_mono_transactions import candidate, freezing, setup_store


async def ready(store, owner):
    change = candidate(owner)
    commit = freezing(change)
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
        await store.research.freeze_and_create_run(commit, tx)
    return commit


async def claim(store, worker, **options):
    async with store.transaction() as tx:
        return await store.research.claim_run(worker, tx, **options)


async def test_two_workers_cannot_claim_the_same_run(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    workers = [str(uuid4()), str(uuid4())]
    results = await asyncio.gather(
        *(claim(store, worker) for worker in workers), return_exceptions=True
    )
    assert not any(isinstance(item, BaseException) for item in results)
    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1
    assert claimed[0].owner_id == user.user_id
    assert claimed[0].run.run_id == commit.run.run_id
    assert claimed[0].run.status == "running" and claimed[0].run.lease_token == 1
    assert claimed[0].run.attempt_count == 1 and claimed[0].run.checkpoint_seq == 1
    assert await pool.fetchval("SELECT status FROM sessions") == "running"
    assert await pool.fetchval("SELECT revision FROM sessions") == 3


async def test_pg_capacity_is_global_two_and_one_per_owner(pg_database):
    pool, store, first = await setup_store(pg_database)
    users = [first]
    async with store.transaction() as tx:
        for _ in range(2):
            other = User.model_validate(
                first.model_dump()
                | {
                    "user_id": uuid4(),
                    "email": str(uuid4()) + "@example.org",
                }
            )
            await store.users.create(other, tx)
            users.append(other)
    for user in users:
        await ready(store, user.user_id)
        await ready(store, user.user_id)
    results = await asyncio.gather(
        *(claim(store, str(uuid4())) for _ in range(6)), return_exceptions=True
    )
    assert not any(isinstance(item, BaseException) for item in results)
    claimed = [item for item in results if item is not None]
    assert len(claimed) == 2
    assert len({item.owner_id for item in claimed}) == 2
    assert await pool.fetchval("SELECT count(*) FROM research_runs WHERE status='running'") == 2
    assert await pool.fetchval("SELECT count(*) FROM research_runs WHERE status='ready'") == 4


async def test_cli_claim_is_owner_and_run_scoped(pg_database):
    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    assert await claim(store, str(uuid4()), owner=uuid4(), run_id=commit.run.run_id) is None
    assert await claim(store, str(uuid4()), owner=user.user_id, run_id=uuid4()) is None
    claimed = await claim(store, str(uuid4()), owner=user.user_id, run_id=commit.run.run_id)
    assert claimed.run.run_id == commit.run.run_id


async def test_renew_uses_pg_time_and_rejects_old_worker_token(pg_database):
    pool, store, user = await setup_store(pg_database)
    await ready(store, user.user_id)
    claimed = await claim(store, str(uuid4()))
    async with store.transaction() as tx:
        renewed = await store.research.renew_lease(claimed, tx)
    assert renewed.run.lease_expires_at > claimed.run.lease_expires_at
    for fields in ({"lease_owner": str(uuid4())}, {"lease_token": 0}):
        old = type(claimed).model_validate(
            claimed.model_dump() | {"run": claimed.run.model_dump() | fields}
        )
        with pytest.raises(AppError, match="stale_resource"):
            async with store.transaction() as tx:
                await store.research.renew_lease(old, tx)
    assert await pool.fetchval("SELECT lease_token FROM research_runs") == 1


async def test_expired_lease_is_not_revived_and_run_is_not_auto_reclaimed(pg_database):
    pool, store, user = await setup_store(pg_database)
    await ready(store, user.user_id)
    claimed = await claim(store, str(uuid4()))
    # Deterministic failure injection in this isolated database; no wall-clock wait.
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            await store.research.renew_lease(claimed, tx)
    assert await claim(store, str(uuid4())) is None
    assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1


async def test_initial_latest_checkpoint_is_owner_scoped(pg_database):
    _pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    snapshot = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    assert snapshot.seq == 1 and snapshot.snapshot_id == commit.checkpoint.snapshot_id
    assert await store.research.load_latest_checkpoint(uuid4(), commit.run.run_id) is None


async def test_claim_session_failure_rolls_back_run_lease_and_capacity(pg_database):
    pool, store, user = await setup_store(pg_database)
    await ready(store, user.user_id)
    await pool.execute("""
        CREATE FUNCTION reject_test_claim() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.status='running' THEN RAISE EXCEPTION 'test fault' USING ERRCODE='22012'; END IF;
        RETURN NEW; END $$;
        CREATE TRIGGER reject_test_claim BEFORE UPDATE ON sessions
        FOR EACH ROW EXECUTE FUNCTION reject_test_claim();
    """)
    with pytest.raises(AdapterError, match="dependency_unavailable"):
        await claim(store, str(uuid4()))
    assert await pool.fetchval("SELECT status FROM research_runs") == "ready"
    assert await pool.fetchval("SELECT lease_token FROM research_runs") == 0
    assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 0
    assert await pool.fetchval("SELECT lease_owner FROM research_runs") is None
    assert await pool.fetchval("SELECT status FROM sessions") == "ready"


def checkpoint(commit, seq, *, phase="plan", used=0, elapsed=0):
    value = commit.checkpoint.state.model_dump(mode="json")
    value["phase"] = phase
    if phase != "plan":
        value["section_plans"] = [
            {
                "section_id": f"section_{index}",
                "title": "Controlled section",
                "objective": "Validate checkpoint mechanics, not research quality",
                "claim_specs": [
                    {
                        "spec_id": f"spec-{index}",
                        "text": "Controlled claim",
                        "required_conditions": [],
                        "required_source_tiers": [],
                    }
                ],
                "sub_questions": ["Controlled query"],
                "retrieval_anchors": [],
                "evidence_requirements": [],
                "analysis_requirements": [],
            }
            for index in range(1, 6)
        ]
    value["run_metadata"]["budget_used"]["search_calls"] = used
    value["run_metadata"]["budget_used"]["elapsed_s"] = elapsed
    state = PipelineState.model_validate(value)
    return Checkpoint(
        snapshot_id=uuid4(),
        run_id=commit.run.run_id,
        seq=seq,
        schema_version=1,
        phase=phase,
        state=state,
        state_hash=canonical_hash(state),
        created_at=datetime.now(UTC),
    )


async def test_checkpoint_seq_survives_rework_to_an_earlier_phase(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    leased = await claim(store, str(uuid4()))
    for seq, phase, used in [(2, "research", 1), (3, "analyze", 2), (4, "research", 2)]:
        point = checkpoint(commit, seq, phase=phase, used=used, elapsed=seq)
        async with store.transaction() as tx:
            leased = await store.research.commit_checkpoint(leased, seq - 1, point, tx)
    latest = await store.research.load_latest_checkpoint(user.user_id, leased.run.run_id)
    assert latest.seq == 4 and latest.phase == "research"
    assert leased.run.phase == "research" and leased.run.checkpoint_seq == 4
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 4
    assert (
        await store.research.load_checkpoint(user.user_id, leased.run.run_id, 3)
    ).phase == "analyze"
    assert (
        await store.research.load_checkpoint(user.user_id, leased.run.run_id, 1)
    ) == commit.checkpoint


@pytest.mark.parametrize("fault", ["token", "expiry", "sequence", "budget", "frozen_brief"])
async def test_checkpoint_rejects_old_lease_seq_budget_or_changed_frozen_input(pg_database, fault):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    leased = await claim(store, str(uuid4()))
    point = checkpoint(commit, 2, used=1, elapsed=2)
    async with store.transaction() as tx:
        leased = await store.research.commit_checkpoint(leased, 1, point, tx)
    next_point = checkpoint(commit, 3, used=1, elapsed=3)
    expected = 2
    if fault == "token":
        leased = type(leased).model_validate(
            leased.model_dump()
            | {"run": leased.run.model_dump() | {"lease_token": leased.run.lease_token + 1}}
        )
    elif fault == "expiry":
        await pool.execute(
            "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
    elif fault == "sequence":
        expected = 1
    elif fault == "budget":
        next_point = checkpoint(commit, 3, used=0, elapsed=3)
    else:
        data = next_point.state.model_dump(mode="json")
        data["research_brief"]["decision_goal"] = "An unauthorized changed decision"
        data["brief_hash"] = canonical_hash(data["research_brief"])
        state = PipelineState.model_validate(data)
        next_point = Checkpoint.model_validate(
            next_point.model_dump()
            | {
                "state": state,
                "state_hash": canonical_hash(state),
            }
        )
    with pytest.raises(AppError, match="stale_resource|invalid_state"):
        async with store.transaction() as tx:
            await store.research.commit_checkpoint(leased, expected, next_point, tx)
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 2
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 2
    assert await pool.fetchval("SELECT status FROM sessions") == "running"


async def test_checkpoint_sql_fault_does_not_advance_run_or_session(pg_database, monkeypatch):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    leased = await claim(store, str(uuid4()))
    original = research_postgres.insert

    async def inject(conn, table, data, suffix=""):
        if table == "phase_snapshots":
            await conn.execute("SELECT 1/0")
        return await original(conn, table, data, suffix)

    monkeypatch.setattr(research_postgres, "insert", inject)
    with pytest.raises(AdapterError):
        async with store.transaction() as tx:
            await store.research.commit_checkpoint(leased, 1, checkpoint(commit, 2), tx)
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 1
    assert await pool.fetchval("SELECT revision FROM sessions") == 3
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
