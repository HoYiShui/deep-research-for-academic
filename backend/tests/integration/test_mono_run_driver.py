"""Real PG/MinIO phase loop and Runner; controlled business output, not A6."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from application.errors import AppError
from application.phase_executor import PhaseExecutor
from application.phase_workers import plan_worker
from application.report_serializer import ReportPublisher
from application.run_driver import RunDriver
from domain.research.phase_contracts import PhaseResult
from domain.research.run_events import ProgressFrame
from infrastructure.clock import SystemClock
from tests.integration.test_mono_phase_tools import setup
from tests.integration.test_mono_run_lifecycle import cancel, claim, resume
from tests.integration.test_mono_task_runner import runner, wait_for_status
from tests.report_fixtures import insufficient_drafts, proposal_claim
from tests.unit.test_phase_contracts import issue, writing_state


def controlled_worker(visits, *, rework=False, before_worker=None):
    async def controlled(value, context):
        visits.append((value.phase, context.unit.unit_id))
        if before_worker:
            await before_worker(value, context)
        changes, degradations = {}, []
        if value.phase == "plan":
            return await plan_worker(value, context)
        if value.phase == "research" and context.unit.parameters["kind"] == "coverage":
            section = context.unit.section_ids[0]
            changes = {"section_coverage": {section: writing_state().section_coverage[section]}}
            if section == "section_3":
                changes["claims"] = {"c-proposal": proposal_claim()}
        elif value.phase == "analyze":
            degradations = [
                {
                    "source": "controlled-analysis",
                    "reason": "No quantitative requirements in controlled fixture",
                    "operation": "analysis_skipped",
                    "section_id": None,
                    "occurred_at": datetime.now(UTC),
                }
            ]
        elif value.phase == "write":
            version = value.values["draft_version"] + 1
            changes = {
                "draft_version": version,
                "draft_sections": {
                    key: section
                    for key, section in insufficient_drafts(
                        version, value.values["research_brief"].task_type
                    ).items()
                    if key in context.unit.section_ids
                },
                "draft_claim_bindings": [],
            }
        elif value.phase == "review":
            feedback = []
            if rework and value.values["draft_version"] == 1:
                feedback = [issue() | {"issue_type": "overclaim"}]
            elif value.values["critic_feedback"]:
                feedback = [
                    item.model_dump()
                    | {
                        "resolved": True,
                        "resolution": "Controlled current-version verification",
                        "resolved_in_version": value.values["draft_version"],
                    }
                    for item in value.values["critic_feedback"]
                ]
            changes = {
                "critic_feedback": feedback,
                "reviewed_draft_version": value.values["draft_version"],
                "review_verdict": "needs_more_work",
            }
        return PhaseResult(
            phase=value.phase,
            unit_id=context.unit_id,
            input_hash=value.semantic_hash,
            changes=changes,
            degradations=degradations,
            failures=[],
        )

    return controlled


async def test_query_progress_is_scoped_and_completion_follows_checkpoint(
    pg_database, object_cache
):
    _, store, user, commit, claimed, _, _, _, _, _, driver = await world(pg_database, object_cache)
    events = []
    execution = driver()
    execution.diagnostic = events.append
    await execution.execute(claimed, asyncio.Event())
    assert events and all(isinstance(event, ProgressFrame) for event in events)
    starts = [event for event in events if event.stage == "query_started"]
    ends = [event for event in events if event.stage == "query_completed"]
    sections = [event for event in events if event.stage == "section_completed"]
    assert len(starts) == len(ends) == len(sections) == 5
    assert len({event.event_id for event in events}) == len(events)
    for start, end in zip(starts, ends, strict=True):
        assert start.unit_id == end.unit_id
        assert start.session_id == end.session_id == commit.run.session_id
        assert start.run_id == end.run_id == commit.run.run_id
        assert start.phase == end.phase == "research"
        assert end.checkpoint_seq > start.checkpoint_seq
        checkpoint = await store.research.load_checkpoint(
            user.user_id, commit.run.run_id, end.checkpoint_seq
        )
        assert end.unit_id in checkpoint.state.run_metadata.unit_manifest
        assert start.completed_units + 1 == end.completed_units
        assert start.total_units == end.total_units == 10
        assert start.results is None and start.chart is None


async def test_failed_query_emits_start_but_not_completed(pg_database, object_cache):
    async def fail(value, context, *_):
        if value.phase == "research" and context.unit.parameters["kind"] == "query":
            raise AppError("dependency_unavailable", "controlled query failure")

    _, _, _, _, claimed, _, _, _, _, _, driver = await world(
        pg_database, object_cache, before_worker=fail
    )
    events = []
    execution = driver()
    execution.diagnostic = events.append
    with pytest.raises(AppError, match="controlled query failure"):
        await execution.execute(claimed, asyncio.Event())
    assert [event.stage for event in events] == ["query_started"]


async def test_progress_observer_failure_does_not_change_execution(pg_database, object_cache):
    _, store, user, commit, claimed, _, _, _, _, _, driver = await world(pg_database, object_cache)
    execution = driver()

    def broken(event):
        raise RuntimeError("private-observer-error")

    execution.diagnostic = broken
    await execution.execute(claimed, asyncio.Event())
    latest = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    assert latest.phase == "done"


async def test_resume_does_not_advertise_cached_query_as_new_execution(pg_database, object_cache):
    stop = asyncio.Event()

    async def interrupt(value, context, *_):
        if value.phase == "research" and context.unit.parameters["kind"] == "query":
            stop.set()

    pool, store, user, commit, claimed, _, _, _, _, visits, driver = await world(
        pg_database, object_cache, before_worker=interrupt
    )
    with pytest.raises(asyncio.CancelledError):
        await driver().execute(claimed, stop)
    latest = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    completed_query = visits[-1][1]
    assert latest.phase == "research" and completed_query in latest.state.run_metadata.unit_manifest
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, latest.seq)
    newer = await claim(store, str(uuid4()))
    events = []
    execution = driver()
    execution.diagnostic = events.append
    visits.clear()
    await execution.execute(newer, asyncio.Event())
    assert completed_query not in [event.unit_id for event in events]
    assert completed_query not in [unit_id for _, unit_id in visits]
    assert len([event for event in events if event.stage == "query_completed"]) == 4


async def world(
    pg_database,
    object_cache,
    *,
    rework=False,
    before_worker=None,
    publish=None,
    task="evaluation_design",
):
    pool, store, user, commit, claimed, model, binding, _, _ = await setup(
        pg_database, object_cache, task=task
    )
    units, phases, terminal, visits = [], [], [], []

    async def before(value, context):
        if before_worker:
            await before_worker(value, context, store, user, commit)

    controlled = controlled_worker(visits, rework=rework, before_worker=before)

    def driver(publisher=None, **options):
        return RunDriver(
            store=store,
            cache=object_cache,
            executor=PhaseExecutor(
                dict.fromkeys(["plan", "research", "analyze", "write", "review"], controlled)
            ),
            model=binding,
            model_slots=asyncio.Semaphore(2),
            clock=SystemClock(),
            publish=publisher or publish or ReportPublisher(store, SystemClock()).publish,
            unit_committed=units.append,
            phase_committed=phases.append,
            finished=terminal.append,
            diagnostic=lambda event: None,
            **options,
        )

    return pool, store, user, commit, claimed, model, units, phases, terminal, visits, driver


@pytest.mark.parametrize(
    "task", ["idea_exploration", "method_differentiation", "evaluation_design"]
)
async def test_full_driver_commits_units_then_routes_then_publishes(
    pg_database, object_cache, task
):
    (
        pool,
        store,
        user,
        commit,
        claimed,
        model,
        units,
        phases,
        terminal,
        visits,
        driver,
    ) = await world(pg_database, object_cache, task=task)
    await driver().execute(claimed, asyncio.Event())
    latest = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    assert latest.phase == "done" and latest.seq == 20
    assert [value.checkpoint.phase for value in phases] == [
        "research",
        "analyze",
        "write",
        "review",
    ]
    assert len(units) == len(visits) == 14
    assert len(terminal) == 1 and terminal[0].status == "completed"
    assert await pool.fetchval("SELECT count(*) FROM reports") == 1
    assert await pool.fetchval("SELECT status FROM sessions") == "completed"
    assert len(model.prompts) == 1 and latest.state.run_metadata.budget_used.tokens == 70
    assert latest.state.final_report.review_verdict == "needs_more_work"
    assert latest.state.final_report.sections["section_3"].task_payload.task_type == task


async def test_driver_rework_keeps_seq_and_updates_global_draft_without_repeating_plan(
    pg_database, object_cache
):
    _, store, user, commit, claimed, model, units, phases, terminal, visits, driver = await world(
        pg_database, object_cache, rework=True
    )
    await driver().execute(claimed, asyncio.Event())
    latest = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    assert latest.seq == 24 and latest.state.draft_version == 2
    assert latest.state.run_metadata.rework_count == 1
    assert [value.checkpoint.phase for value in phases] == [
        "research",
        "analyze",
        "write",
        "review",
        "write",
        "review",
    ]
    assert len(model.prompts) == 1 and len(units) == 16 and len(terminal) == 1
    assert len({unit for _, unit in visits}) == len(visits)
    assert latest.state.critic_feedback[0].resolved_in_version == 2


async def test_cancellation_after_io_commits_safe_unit_but_never_advances_or_publishes(
    pg_database, object_cache
):
    async def stopping(value, context, store, user, commit):
        if value.phase == "research":
            await cancel(store, user.user_id, commit.session.session_id)

    pool, store, user, commit, claimed, model, units, phases, terminal, _, driver = await world(
        pg_database, object_cache, before_worker=stopping
    )
    await driver().execute(claimed, asyncio.Event())
    latest = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    assert latest.seq == 4 and latest.phase == "research"
    assert len(units) == 2 and len(phases) == 1
    assert [run.status for run in terminal] == ["cancelled"]
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert len(model.prompts) == 1


async def test_stop_after_committed_unit_resumes_by_verifying_manifest_without_repaying(
    pg_database, object_cache
):
    stop = asyncio.Event()

    async def interrupt(value, context, *_):
        if value.phase == "plan":
            # Stop during a completed unit; allow its metered output and safe commit.
            stop.set()

    (
        pool,
        store,
        user,
        commit,
        claimed,
        model,
        units,
        _phases,
        terminal,
        visits,
        driver,
    ) = await world(pg_database, object_cache, before_worker=interrupt)
    with pytest.raises(asyncio.CancelledError):
        await driver().execute(claimed, stop)
    # Simulate scanner-observed dead lease, not a request to rerun automatically.
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, 2)
    newer = await claim(store, str(uuid4()))
    visits.clear()
    await driver().execute(newer, asyncio.Event())
    assert not any(phase == "plan" for phase, _ in visits)
    assert len(model.prompts) == 1 and len(units) == 14 and len(terminal) == 1
    assert (await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)).seq == 20
    assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 2


async def test_returning_publisher_is_not_completion(pg_database, object_cache):
    async def no_publication(*_):
        return None

    pool, _, _, _, claimed, _, _, _, terminal, _, driver = await world(pg_database, object_cache)
    with pytest.raises(AppError, match="terminal facts"):
        await driver(no_publication).execute(claimed, asyncio.Event())
    assert terminal == [] and await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert await pool.fetchval("SELECT status FROM research_runs") == "running"


async def test_runner_with_driver_requires_persisted_completion_not_coroutine_return(
    pg_database, object_cache
):
    # setup already claimed: release this isolated fixture's lease by explicit failure/resume.
    pool, store, user, commit, _, model, _, _, terminal, _, driver = await world(
        pg_database, object_cache
    )
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, 1)
    worker = runner(store, driver().execute)
    try:
        await worker.tick()
        run = await wait_for_status(store, user.user_id, commit.run.run_id, "completed")
        if worker.active is not None:
            await asyncio.wait_for(worker.active, timeout=5)
        assert run.phase == "done" and run.failure is None
        assert len(model.prompts) == 1 and len(terminal) == 1
        assert await pool.fetchval("SELECT count(*) FROM reports") == 1
    finally:
        await worker.aclose()


async def test_cancel_before_driver_starts_is_terminal_with_zero_worker_or_model_calls(
    pg_database, object_cache
):
    (
        pool,
        store,
        user,
        commit,
        claimed,
        model,
        units,
        phases,
        terminal,
        visits,
        driver,
    ) = await world(pg_database, object_cache)
    await cancel(store, user.user_id, commit.session.session_id)
    await driver().execute(claimed, asyncio.Event())
    assert not model.prompts and not visits and not units and not phases
    assert [run.status for run in terminal] == ["cancelled"]
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_cancellation_racing_with_phase_advance_is_not_execution_failure(
    pg_database, object_cache, monkeypatch
):
    from application.orchestrator import RunUnitCoordinator

    pool, store, user, commit, claimed, model, units, phases, terminal, _, driver = await world(
        pg_database, object_cache
    )
    original = RunUnitCoordinator.advance_phase

    async def race(self, current, projected):
        await cancel(store, user.user_id, commit.session.session_id)
        return await original(self, current, projected)

    monkeypatch.setattr(RunUnitCoordinator, "advance_phase", race)
    await driver().execute(claimed, asyncio.Event())
    assert len(units) == 1 and not phases and len(model.prompts) == 1
    assert [run.status for run in terminal] == ["cancelled"]
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 2
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_cancellation_racing_with_publication_cannot_emit_completed(
    pg_database, object_cache
):
    pool, store, user, commit, claimed, _, _, _, terminal, _, driver = await world(
        pg_database, object_cache
    )

    async def race(*_):
        await cancel(store, user.user_id, commit.session.session_id)
        raise AppError("invalid_session_state", "Publication rejected cancellation")

    await driver(race).execute(claimed, asyncio.Event())
    assert [run.status for run in terminal] == ["cancelled"]
    assert await pool.fetchval("SELECT phase FROM research_runs") == "review"
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_projection_error_after_publication_does_not_change_pg_completion(
    pg_database, object_cache
):
    pool, _, _, _, claimed, _, _, _, _, _, driver = await world(pg_database, object_cache)
    execution = driver()

    def unavailable(_):
        raise RuntimeError("ephemeral projection offline")

    execution.finished = unavailable
    await execution.execute(claimed, asyncio.Event())
    assert await pool.fetchval("SELECT status FROM research_runs") == "completed"
    assert await pool.fetchval("SELECT count(*) FROM reports") == 1


async def test_one_run_tool_clock_and_authority_follow_phase_cursor(pg_database, object_cache):
    async def write_model(value, context, *_):
        if value.phase == "write":
            await context.invoke(
                "llm", {"phase": "write", "prompt": "Controlled write clock probe"}
            )

    pool, store, user, commit, claimed, model, units, _, _, _, driver = await world(
        pg_database, object_cache, before_worker=write_model
    )
    await driver().execute(claimed, asyncio.Event())
    latest = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    assert len(model.prompts) == 2 and latest.state.run_metadata.budget_used.tokens == 140
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 2
    elapsed = [unit.checkpoint.state.run_metadata.budget_used.elapsed_s for unit in units]
    assert elapsed == sorted(elapsed) and elapsed[-1] >= elapsed[0] > 0


async def test_publisher_fault_runner_records_failed_without_done_or_report(
    pg_database, object_cache
):
    from domain.ports import AdapterError

    async def broken(*_):
        raise AdapterError(
            "postgres", "dependency_unavailable", "secret-connection-string", True, "publish"
        )

    pool, store, user, commit, _, _, _, _, terminal, _, driver = await world(
        pg_database, object_cache
    )
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    await resume(store, user.user_id, commit, 1)
    worker = runner(store, driver(broken).execute)
    try:
        await worker.tick()
        run = await wait_for_status(store, user.user_id, commit.run.run_id, "failed")
        assert run.failure.code == "dependency_unavailable" and run.resume_allowed
        assert "secret" not in run.failure.model_dump_json()
        assert run.phase == "review" and run.checkpoint_seq == 19
        assert terminal == [] and await pool.fetchval("SELECT count(*) FROM reports") == 0
    finally:
        await worker.aclose()


@pytest.mark.parametrize(
    "changed", ["owner", "run", "session", "lease_owner", "lease_token", "brief_hash", "config"]
)
async def test_tool_cursor_cannot_rebind_execution_authority(pg_database, object_cache, changed):
    from application.records import ClaimedRun
    from application.tool_calls import ToolCallService

    pool, store, _, _, claimed, _, _, _, _, _, _ = await world(pg_database, object_cache)
    service = ToolCallService(
        claimed, store, store.research, object_cache, SystemClock(), elapsed_base=7
    )
    values = claimed.model_dump()
    if changed == "owner":
        values["owner_id"] = uuid4()
    elif changed in {"run", "session"}:
        values["run"][changed + "_id"] = uuid4()
    elif changed == "lease_owner":
        values["run"]["lease_owner"] = str(uuid4())
    elif changed == "lease_token":
        values["run"]["lease_token"] += 1
    elif changed == "brief_hash":
        values["run"]["brief_hash"] = "f" * 64
    else:
        values["run"]["config_snapshot"]["versions"]["llm_revision"] = "changed-revision"
    with pytest.raises(AppError, match="authority"):
        service.update_claimed(ClaimedRun.model_validate(values))
    assert service.claimed == claimed and service.elapsed_s() >= 7
    assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 0


async def test_cursor_advance_preserves_timer_and_rejects_backwards_sequence(
    pg_database, object_cache
):
    from application.records import ClaimedRun
    from application.tool_calls import ToolCallService

    _, store, _, _, claimed, _, _, _, _, _, _ = await world(pg_database, object_cache)

    class ControlledClock:
        tick = 100

        def monotonic(self):
            return self.tick

    clock = ControlledClock()
    service = ToolCallService(claimed, store, store.research, object_cache, clock, elapsed_base=7)
    values = claimed.model_dump()
    values["run"] |= {"checkpoint_seq": 2, "phase": "research"}
    clock.tick = 109
    service.update_claimed(ClaimedRun.model_validate(values))
    assert service.elapsed_s() == 16 and service.claimed.run.phase == "research"
    with pytest.raises(AppError, match="authority"):
        service.update_claimed(claimed)
    assert service.elapsed_s() == 16
