"""Real PG atomic publication mechanics, not research-quality acceptance."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from application.errors import AppError
from application.report_serializer import serialize_report
from domain.ports import AdapterError
from domain.research.ids import canonical_hash
from domain.research.state import Checkpoint, PipelineState
from tests.integration.test_mono_run_lifecycle import cancel, checkpoint, claim, ready
from tests.integration.test_mono_transactions import setup_store
from tests.report_fixtures import insufficient_review


async def reviewed(store, owner):
    commit = await ready(store, owner)
    claimed = await claim(store, str(uuid4()))
    state = insufficient_review(checkpoint(commit, 2, phase="review").state)
    point = Checkpoint(
        snapshot_id=uuid4(),
        run_id=commit.run.run_id,
        seq=2,
        schema_version=1,
        phase="review",
        state=state,
        state_hash=canonical_hash(state),
        created_at=datetime.now(UTC),
    )
    async with store.transaction() as tx:
        claimed = await store.research.commit_checkpoint(claimed, 1, point, tx)
    report = serialize_report(state, report_id=uuid4(), created_at=datetime.now(UTC))
    final_state = PipelineState.model_validate(
        state.model_dump() | {"phase": "done", "final_report": report}
    )
    final = Checkpoint(
        snapshot_id=uuid4(),
        run_id=commit.run.run_id,
        seq=3,
        schema_version=1,
        phase="done",
        state=final_state,
        state_hash=canonical_hash(final_state),
        created_at=datetime.now(UTC),
    )
    return commit, claimed, final, report


async def publish(store, claimed, final):
    async with store.transaction() as tx:
        return await store.research.publish_report(claimed, 2, final, tx)


async def test_report_and_done_checkpoint_publish_with_run_and_session(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit, claimed, final, report = await reviewed(store, user.user_id)
    result = await publish(store, claimed, final)
    assert result.status == "completed" and result.phase == "done" and result.checkpoint_seq == 3
    assert result.lease_owner is None and result.finished_at is not None
    assert await store.research.load_report(user.user_id, result.run_id) == report
    assert await store.research.load_report(uuid4(), result.run_id) is None
    assert await store.research.load_latest_checkpoint(user.user_id, result.run_id) == final
    assert await pool.fetchval("SELECT status FROM sessions") == "completed"
    with pytest.raises(AppError, match="invalid_session_state"):
        await cancel(store, user.user_id, commit.session.session_id)


async def test_cancel_committed_first_prevents_report_publication(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit, claimed, final, _ = await reviewed(store, user.user_id)
    await cancel(store, user.user_id, commit.session.session_id)
    with pytest.raises(AppError, match="invalid_session_state"):
        await publish(store, claimed, final)
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 2
    assert await pool.fetchval("SELECT status FROM sessions") == "cancelling"


@pytest.mark.parametrize("table", ["reports", "phase_snapshots", "research_runs", "sessions"])
async def test_any_publication_write_failure_rolls_back_all_four_facts(pg_database, table):
    pool, store, user = await setup_store(pg_database)
    _, claimed, final, _ = await reviewed(store, user.user_id)
    await pool.execute(f"""
        CREATE FUNCTION reject_test_publish() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'test fault' USING ERRCODE='22012'; END $$;
        CREATE TRIGGER reject_test_publish BEFORE INSERT OR UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_test_publish();
    """)
    with pytest.raises(AdapterError):
        await publish(store, claimed, final)
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 2
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 2
    assert await pool.fetchval("SELECT status FROM research_runs") == "running"
    assert await pool.fetchval("SELECT status FROM sessions") == "running"


async def test_publication_cannot_change_reviewed_facts(pg_database):
    pool, store, user = await setup_store(pg_database)
    _, claimed, final, report = await reviewed(store, user.user_id)
    modified = final.state.model_dump(mode="json")
    modified["draft_sections"]["section_1"]["content"] = "Unreviewed new text"
    state = PipelineState.model_validate(modified)
    changed = Checkpoint.model_validate(
        final.model_dump() | {"state": state, "state_hash": canonical_hash(state)}
    )
    with pytest.raises(AppError, match="invalid_state"):
        await publish(store, claimed, changed)
    assert await store.research.load_report(user.user_id, report.run_id) is None
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 2


async def test_concurrent_cancel_and_publish_have_only_one_committed_outcome(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit, claimed, final, _ = await reviewed(store, user.user_id)
    results = await asyncio.gather(
        publish(store, claimed, final),
        cancel(store, user.user_id, commit.session.session_id),
        return_exceptions=True,
    )
    assert (
        len(
            [
                result
                for result in results
                if isinstance(result, AppError) and result.code == "invalid_session_state"
            ]
        )
        == 1
    )
    assert len([result for result in results if not isinstance(result, BaseException)]) == 1
    status = await pool.fetchval("SELECT status FROM research_runs")
    assert await pool.fetchval("SELECT status FROM sessions") == status
    assert status in {"completed", "cancelling"}
    assert await pool.fetchval("SELECT count(*) FROM reports") == (status == "completed")
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == (
        3 if status == "completed" else 2
    )


@pytest.mark.parametrize("fence", ["token", "expiry", "sequence"])
async def test_report_publication_rejects_stale_worker_or_sequence(pg_database, fence):
    pool, store, user = await setup_store(pg_database)
    _, claimed, final, _ = await reviewed(store, user.user_id)
    seq = 2
    if fence == "token":
        claimed = type(claimed).model_validate(
            claimed.model_dump() | {"run": claimed.run.model_dump() | {"lease_token": 0}}
        )
    elif fence == "expiry":
        await pool.execute(
            "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
    else:
        seq = 1
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            await store.research.publish_report(claimed, seq, final, tx)
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 2


@pytest.mark.parametrize("field", ["markdown", "references", "risks", "title"])
async def test_repository_rechecks_deterministic_report_not_only_schema(pg_database, field):
    pool, store, user = await setup_store(pg_database)
    _, claimed, final, report = await reviewed(store, user.user_id)
    replacement = {
        "markdown": report.markdown + "\nUnreviewed extra conclusion",
        "references": [
            {
                "reference_id": "R9",
                "source_id": "unknown",
                "title": "Invented citation",
                "canonical_url": "https://example.org/fake",
                "version": None,
                "locations": [],
                "evidence_ids": [],
            }
        ],
        "risks": [],
        "title": "Unreviewed title",
    }[field]
    altered = type(report).model_validate(report.model_dump() | {field: replacement})
    state = PipelineState.model_validate(final.state.model_dump() | {"final_report": altered})
    point = Checkpoint.model_validate(
        final.model_dump() | {"state": state, "state_hash": canonical_hash(state)}
    )
    with pytest.raises(AppError, match="invalid_state"):
        await publish(store, claimed, point)
    assert await pool.fetchval("SELECT status FROM research_runs") == "running"
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 2
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 2
