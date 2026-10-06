"""Lease-owned phase loop; explicit workers/tools/publisher, no implicit fallback.

The composition root supplies a quality-gated atomic publisher. This module
does not turn a review candidate, a returned coroutine, or an SSE event into
completion. Tool time spans the entire execution, not each phase or unit.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from application.errors import AppError
from application.fetch_tools import FetchBinding
from application.orchestrator import RunUnitCoordinator
from application.phase_executor import ExecutionContext, PhaseExecutor
from application.phase_tools import ModelBinding, PhaseTools
from application.phase_units import PhaseTransition, UnitCommit, plan_units
from application.records import ClaimedRun
from application.search_tools import SearchBinding
from application.tool_calls import ToolCallService
from domain.content import ResultCachePort
from domain.ports import ClockPort
from domain.research.machine import TERMINAL_REASONS
from domain.research.models import ResearchRun
from domain.research.state import Checkpoint

Publisher = Callable[[ClaimedRun, Checkpoint], Awaitable[None]]
logger = logging.getLogger(__name__)


class RunDriver:
    def __init__(
        self,
        *,
        store,
        cache: ResultCachePort,
        executor: PhaseExecutor,
        model: ModelBinding,
        model_slots: asyncio.Semaphore,
        clock: ClockPort,
        publish: Publisher,
        unit_committed: Callable[[UnitCommit], None],
        phase_committed: Callable[[PhaseTransition], None],
        finished: Callable[[ResearchRun], None],
        diagnostic: Callable[[object], None],
        allow_uncertain_replay=False,
        search: SearchBinding | None = None,
        fetch: FetchBinding | None = None,
    ):
        if not callable(publish):
            raise TypeError("An explicit quality-gated atomic publisher is required")
        if type(allow_uncertain_replay) is not bool:
            raise TypeError("Replay permission must be an explicit coordinator decision")
        self.store, self.cache, self.executor = store, cache, executor
        self.model, self.model_slots, self.clock = model, model_slots, clock
        self.publish = publish
        self.unit_committed, self.phase_committed = unit_committed, phase_committed
        self.finished, self.diagnostic = finished, diagnostic
        self.allow_uncertain_replay = allow_uncertain_replay
        self.search = search
        self.fetch = fetch

    def _project_finished(self, run):
        try:
            self.finished(run)
        except Exception:  # noqa: BLE001 -- PG terminal facts cannot be undone by projection
            logger.warning("run_projection_failed")

    async def _stop_if_requested(self, claimed, stop):
        if stop.is_set():
            # Runner persists interrupted, not cancelled: this is process shutdown.
            raise asyncio.CancelledError
        async with self.store.transaction() as tx:
            run = await self.store.research.check_run_lease(claimed, tx)
            if run.status == "cancelling" or run.cancel_requested_at is not None:
                cancelled = await self.store.research.finish_cancelled(claimed, tx)
            else:
                cancelled = None
        if cancelled is not None:
            self._project_finished(cancelled)
            return True
        return False

    async def _verify_published(self, claimed, point):
        async with self.store.transaction() as tx:
            run = await self.store.research.get_run(claimed.owner_id, claimed.run.run_id, tx)
            latest = await self.store.research.load_latest_checkpoint(
                claimed.owner_id, claimed.run.run_id, tx
            )
            report = await self.store.research.load_report(claimed.owner_id, claimed.run.run_id, tx)
            session = await self.store.research.get_session(
                claimed.owner_id, claimed.run.session_id, tx
            )
        if (
            run is None
            or run.status != "completed"
            or run.phase != "done"
            or run.checkpoint_seq != point.seq + 1
            or latest is None
            or latest.seq != run.checkpoint_seq
            or latest.phase != "done"
            or report is None
            or latest.state.final_report != report
            or session is None
            or session.status != "completed"
            or session.run_id != run.run_id
        ):
            raise AppError("invalid_state", "Publisher did not commit complete terminal facts")
        self._project_finished(run)

    async def execute(self, claimed: ClaimedRun, stop: asyncio.Event):
        # PG time determines ownership. No model/config binding is constructed
        # for a cancelled execution and no SDK starts while this transaction lives.
        if await self._stop_if_requested(claimed, stop):
            return
        async with self.store.transaction() as tx:
            run = await self.store.research.check_run_lease(claimed, tx)
            point = await self.store.research.load_latest_checkpoint(
                claimed.owner_id, run.run_id, tx
            )
            budget = await self.store.research.load_tool_budget(claimed.owner_id, run.run_id, tx)
        if point is None or point.seq != run.checkpoint_seq:
            raise AppError("invalid_state", "Run has no current complete checkpoint")
        claimed = ClaimedRun(owner_id=claimed.owner_id, run=run)
        elapsed = max(
            point.state.run_metadata.budget_used.elapsed_s,
            budget.used.elapsed_s if budget is not None else 0,
        )
        calls = ToolCallService(
            claimed,
            self.store,
            self.store.research,
            self.cache,
            self.clock,
            elapsed_base=elapsed,
        )
        tools = PhaseTools(
            calls,
            self.model,
            point.state.run_metadata.knowledge_snapshot,
            model_slots=self.model_slots,
            search=self.search,
            fetch=self.fetch,
        )
        coordinator = RunUnitCoordinator(
            store=self.store,
            cache=self.cache,
            executor=self.executor,
            clock=self.clock,
            elapsed_s=calls.elapsed_s,
            committed=self.unit_committed,
        )

        def context(current, checkpoint, scope, value):
            calls.update_claimed(current)

            async def stopping():
                if stop.is_set():
                    return True
                async with self.store.transaction() as tx:
                    owned = await self.store.research.check_run_lease(current, tx)
                return owned.status != "running" or owned.cancel_requested_at is not None

            return ExecutionContext(
                owner_id=current.owner_id,
                run_id=current.run.run_id,
                config=current.run.config_snapshot,
                brief_hash=current.run.brief_hash,
                lease_token=current.run.lease_token,
                unit_id=scope.unit_id,
                unit=scope,
                deadline=self.clock.now_utc()
                + timedelta(
                    seconds=current.run.config_snapshot.limits.deadline_s - calls.elapsed_s()
                ),
                cancel_check=stopping,
                invoke=tools.for_phase(
                    value,
                    unit_scope=scope,
                    terminal=checkpoint.state.run_metadata.stop_reason in TERMINAL_REASONS,
                    allow_uncertain_replay=self.allow_uncertain_replay,
                ),
                emit=self.diagnostic,
            )

        while True:
            if await self._stop_if_requested(claimed, stop):
                return
            claimed, point = await coordinator.load_owned(claimed)
            for unit in plan_units(point.state):
                if await self._stop_if_requested(claimed, stop):
                    return
                try:
                    committed = await coordinator.execute_unit(claimed, unit, context)
                except AppError as exc:
                    if exc.code == "invalid_session_state" and await self._stop_if_requested(
                        claimed, stop
                    ):
                        return
                    raise
                claimed, point = committed.claimed, committed.checkpoint
            if await self._stop_if_requested(claimed, stop):
                return
            try:
                transition = await coordinator.advance_phase(claimed, self.phase_committed)
            except AppError as exc:
                if exc.code == "invalid_session_state" and await self._stop_if_requested(
                    claimed, stop
                ):
                    return
                raise
            claimed, point = transition.claimed, transition.checkpoint
            if not transition.decision.deliver:
                continue
            if await self._stop_if_requested(claimed, stop):
                return
            try:
                await self.publish(claimed, point)
            except AppError as exc:
                if exc.code == "invalid_session_state" and await self._stop_if_requested(
                    claimed, stop
                ):
                    return
                raise
            await self._verify_published(claimed, point)
            return
