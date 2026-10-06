"""Full unit snapshots and verified skip: actual PG/MinIO, controlled model."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from application.errors import AppError
from application.orchestrator import RunUnitCoordinator
from application.phase_executor import ExecutionContext, PhaseExecutor
from application.phase_tools import PhaseTools
from application.phase_units import UnitScope
from application.phase_workers import plan_worker
from domain.ports import AdapterError
from domain.research.phase_contracts import PhaseResult
from infrastructure.clock import SystemClock
from tests.integration.test_mono_phase_tools import setup
from tests.integration.test_mono_run_lifecycle import cancel, claim, resume
from tests.integration.test_mono_tool_cache import service


async def world(pg_database, object_cache, *, worker=plan_worker):
    pool, store, user, commit, claimed, model, binding, _tools, _value = await setup(
        pg_database, object_cache
    )
    calls = service(store, claimed, object_cache)
    tools = PhaseTools(calls, binding, [], model_slots=asyncio.Semaphore(2))
    unit = UnitScope(
        unit_id="plan:0:all",
        phase="plan",
        section_ids=[f"section_{n}" for n in range(1, 6)],
        requirement_ids=[],
        parameters={},
    )
    projected = []
    coordinator = RunUnitCoordinator(
        store=store,
        cache=object_cache,
        executor=PhaseExecutor({"plan": worker}),
        clock=SystemClock(),
        elapsed_s=calls.elapsed_s,
        committed=projected.append,
    )

    def context(claimed, point, scope, value):
        async def stopping():
            current = await store.research.get_run(claimed.owner_id, claimed.run.run_id)
            return current.status != "running" or current.cancel_requested_at is not None

        return ExecutionContext(
            owner_id=claimed.owner_id,
            run_id=claimed.run.run_id,
            config=claimed.run.config_snapshot,
            brief_hash=claimed.run.brief_hash,
            lease_token=claimed.run.lease_token,
            unit_id=scope.unit_id,
            unit=scope,
            deadline=datetime.now(UTC) + timedelta(minutes=1),
            cancel_check=stopping,
            invoke=tools.for_phase(value, unit_scope=scope),
            emit=lambda event: None,
        )

    return pool, store, user, commit, claimed, model, unit, projected, coordinator, context


async def test_unit_commits_full_state_manifest_and_ledger_without_phase_advance(
    pg_database, object_cache
):
    pool, store, user, commit, claimed, model, unit, projected, coordinator, context = await world(
        pg_database, object_cache
    )
    result = await coordinator.execute_unit(claimed, unit, context)
    assert not result.skipped and result.checkpoint.seq == result.claimed.run.checkpoint_seq == 2
    assert result.checkpoint.phase == result.claimed.run.phase == "plan"
    assert len(result.checkpoint.state.section_plans) == 5
    entry = result.checkpoint.state.run_metadata.unit_manifest[unit.unit_id]
    assert entry.checkpoint_seq == 2 and entry.phase == "plan"
    assert entry.result_ref.key == f"phase-results/{commit.run.run_id}/{entry.result_hash}"
    assert entry.affected_ids == sorted(unit.section_ids)
    assert result.checkpoint.state.run_metadata.budget_used.tokens == 70
    assert len(model.prompts) == 1 and projected == [result]
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 2
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert await pool.fetchval("SELECT status FROM sessions") == "running"
    assert (
        await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
        == result.checkpoint
    )
    skipped = await coordinator.execute_unit(result.claimed, unit, context)
    assert skipped.skipped and skipped.checkpoint == result.checkpoint
    assert len(model.prompts) == 1 and len(projected) == 1


async def test_new_lease_verifies_committed_unit_without_reexecuting_worker(
    pg_database, object_cache
):
    pool, store, user, commit, claimed, model, unit, _projected, coordinator, context = await world(
        pg_database, object_cache
    )
    await coordinator.execute_unit(claimed, unit, context)
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, 2)
    newer = await claim(store, str(uuid4()))
    fresh = RunUnitCoordinator(
        store=store,
        cache=object_cache,
        executor=PhaseExecutor({}),
        clock=SystemClock(),
        elapsed_s=lambda: 0,
        committed=lambda value: None,
    )

    def forbidden(*args):
        raise AssertionError("Committed units must not construct a worker context")

    result = await fresh.execute_unit(newer, unit, forbidden)
    assert result.skipped and result.claimed.run.lease_token == 2
    assert (
        result.checkpoint.seq == 2 and result.checkpoint.state.run_metadata.budget_used.tokens == 70
    )
    assert len(model.prompts) == 1
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1


async def test_missing_committed_unit_object_never_reexecutes_worker(pg_database, object_cache):
    (
        _pool,
        _store,
        _user,
        _commit,
        claimed,
        model,
        unit,
        _projected,
        coordinator,
        context,
    ) = await world(pg_database, object_cache)
    result = await coordinator.execute_unit(claimed, unit, context)
    entry = result.checkpoint.state.run_metadata.unit_manifest[unit.unit_id]
    await asyncio.to_thread(
        object_cache._client.remove_object, object_cache.bucket, entry.result_ref.key
    )
    with pytest.raises(AdapterError, match="content_missing"):
        await coordinator.execute_unit(result.claimed, unit, context)
    assert len(model.prompts) == 1


async def test_same_unit_id_with_different_scope_cannot_skip_committed_work(
    pg_database, object_cache
):
    (
        _pool,
        _store,
        _user,
        _commit,
        claimed,
        model,
        unit,
        _projected,
        coordinator,
        context,
    ) = await world(pg_database, object_cache)
    result = await coordinator.execute_unit(claimed, unit, context)
    wrong = UnitScope.model_validate(unit.model_dump() | {"parameters": {"query": "different"}})
    with pytest.raises(AppError, match="invalid_state"):
        await coordinator.execute_unit(result.claimed, wrong, context)
    assert len(model.prompts) == 1


async def test_checkpoint_failure_does_not_publish_unit_and_retry_reuses_paid_result(
    pg_database, object_cache
):
    (
        pool,
        _store,
        _user,
        _commit,
        claimed,
        model,
        unit,
        projected,
        coordinator,
        context,
    ) = await world(pg_database, object_cache)
    await pool.execute("""
        CREATE FUNCTION reject_unit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.seq>1 THEN RAISE EXCEPTION 'controlled fault' USING ERRCODE='22012'; END IF;
        RETURN NEW; END $$;
        CREATE TRIGGER unit_fault BEFORE INSERT ON phase_snapshots FOR EACH ROW EXECUTE FUNCTION reject_unit();
    """)
    with pytest.raises(AdapterError):
        await coordinator.execute_unit(claimed, unit, context)
    assert projected == []
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
    assert await pool.fetchval("SELECT status FROM tool_calls") == "succeeded"
    assert len(model.prompts) == 1
    await pool.execute("DROP TRIGGER unit_fault ON phase_snapshots")
    result = await coordinator.execute_unit(claimed, unit, context)
    assert result.checkpoint.seq == 2 and len(model.prompts) == 1 and projected == [result]


async def test_empty_plan_cannot_commit_unit_or_progress(pg_database, object_cache):
    async def invalid(value, ctx):
        return PhaseResult(
            phase="plan",
            unit_id=ctx.unit_id,
            input_hash=value.semantic_hash,
            changes={"section_plans": []},
            degradations=[],
            failures=[],
        )

    (
        pool,
        _store,
        _user,
        _commit,
        claimed,
        _model,
        unit,
        projected,
        coordinator,
        context,
    ) = await world(pg_database, object_cache, worker=invalid)
    with pytest.raises(AppError, match="invalid_state"):
        await coordinator.execute_unit(claimed, unit, context)
    assert projected == []
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


@pytest.mark.parametrize("wrong", ["owner_id", "run_id", "lease_token"])
async def test_wrong_context_authority_is_rejected_before_worker(pg_database, object_cache, wrong):
    (
        pool,
        _store,
        _user,
        _commit,
        claimed,
        model,
        unit,
        _projected,
        coordinator,
        context,
    ) = await world(pg_database, object_cache)

    def invalid(*args):
        ctx = context(*args)
        return ExecutionContext.model_validate(
            ctx.model_dump() | {wrong: ctx.lease_token + 1 if wrong == "lease_token" else uuid4()}
        )

    with pytest.raises(AppError, match="invalid_state"):
        await coordinator.execute_unit(claimed, unit, invalid)
    assert model.prompts == []
    assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 0


async def test_cancel_before_unit_starts_blocks_new_io(pg_database, object_cache):
    pool, store, user, commit, claimed, model, unit, _projected, coordinator, context = await world(
        pg_database, object_cache
    )
    await cancel(store, user.user_id, commit.session.session_id)
    with pytest.raises(AppError, match="invalid_session_state"):
        await coordinator.execute_unit(claimed, unit, context)
    assert model.prompts == []
    assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 0


async def test_cancel_after_completed_io_preserves_last_safe_unit_but_not_report(
    pg_database, object_cache
):
    pool, store, user, commit, claimed, model, unit, projected, coordinator, context = await world(
        pg_database, object_cache
    )
    original = model.complete_metered

    async def cancelling(prompt):
        result = await original(prompt)
        await cancel(store, user.user_id, commit.session.session_id)
        return result

    model.complete_metered = cancelling
    result = await coordinator.execute_unit(claimed, unit, context)
    assert result.claimed.run.status == "cancelling" and result.checkpoint.seq == 2
    assert len(result.checkpoint.state.section_plans) == 5
    assert result.checkpoint.state.run_metadata.budget_used.tokens == 70
    assert projected == [result]
    async with store.transaction() as tx:
        await store.research.finish_cancelled(result.claimed, tx)
    assert await pool.fetchval("SELECT status FROM research_runs") == "cancelled"
    assert await pool.fetchval("SELECT phase FROM research_runs") == "plan"
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_expired_worker_cannot_commit_unit_or_emit_success(pg_database, object_cache):
    from tests.unit.test_phase_contracts import plans

    pool = None

    async def expired(value, ctx):
        await pool.execute(
            "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
        return PhaseResult(
            phase="plan",
            unit_id=ctx.unit_id,
            input_hash=value.semantic_hash,
            changes={"section_plans": plans()},
            degradations=[],
            failures=[],
        )

    (
        pool,
        _store,
        _user,
        _commit,
        claimed,
        _model,
        unit,
        projected,
        coordinator,
        context,
    ) = await world(pg_database, object_cache, worker=expired)
    with pytest.raises(AppError, match="stale_resource"):
        await coordinator.execute_unit(claimed, unit, context)
    assert projected == []
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 1


async def test_concurrent_unit_commits_use_expected_seq_and_publish_only_one(
    pg_database, object_cache
):
    from tests.unit.test_phase_contracts import plans

    barrier = asyncio.Event()
    count = 0

    async def worker(value, ctx):
        nonlocal count
        count += 1
        if count == 2:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), timeout=3)
        return PhaseResult(
            phase="plan",
            unit_id=ctx.unit_id,
            input_hash=value.semantic_hash,
            changes={"section_plans": plans()},
            degradations=[],
            failures=[],
        )

    (
        pool,
        _store,
        _user,
        _commit,
        claimed,
        _model,
        unit,
        projected,
        coordinator,
        context,
    ) = await world(pg_database, object_cache, worker=worker)
    results = await asyncio.gather(
        coordinator.execute_unit(claimed, unit, context),
        coordinator.execute_unit(claimed, unit, context),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    rejected = [value for value in results if isinstance(value, Exception)]
    assert isinstance(rejected[0], AppError) and rejected[0].code == "stale_resource"
    assert len(projected) == 1
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 2
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 2


async def test_projection_failure_cannot_undo_committed_unit_or_fail_run(
    pg_database, object_cache, caplog
):
    (
        pool,
        _store,
        _user,
        _commit,
        claimed,
        _model,
        unit,
        _projected,
        coordinator,
        context,
    ) = await world(pg_database, object_cache)

    def broken(value):
        raise RuntimeError("SECRET-raw-projection-error")

    coordinator.committed = broken
    result = await coordinator.execute_unit(claimed, unit, context)
    assert not result.skipped and result.checkpoint.seq == 2
    assert await pool.fetchval("SELECT status FROM research_runs") == "running"
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 2
    assert "unit_projection_failed" in caplog.text and "SECRET" not in caplog.text
