"""Pipeline loop: drives the explicit state machine and emits events.

The orchestrator is the sole control flow. It runs plan -> research -> analyze
-> write -> review (with rework routing via the policy table), snapshots the
SSOT at every phase boundary, and weaves in the per-dependency failure
semantics (LLM exhaustion terminates; persistence failure terminates; search
and Milvus degrade without terminating).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from application.errors import AppError
from application.phase_executor import ExecutionContext, PhaseExecutor
from application.phase_units import UnitCommit, UnitEnvelope, UnitScope
from application.ports import CancellationPort, StateStorePort
from application.records import ClaimedRun
from application.sse import EventBus
from domain.ports import CodeExecutionPort, LLMPort, RetrievalPort, SearchPort
from domain.research.agents import architect, code_crafter, critic, data_analyst, scout, writer
from domain.research.events import DoneEvent, ErrorEvent, PhaseEvent, ReworkEvent
from domain.research.ids import canonical_hash
from domain.research.legacy_state import PipelineState
from domain.research.machine import WORKERS, next_phase, phase_after_review, route_after_review
from domain.research.phase_contracts import PhaseInput, merge_phase_result
from domain.research.state import BudgetUsage, Checkpoint, UnitResult
from domain.research.state import PipelineState as MonoState

# Cap on rework loops so an unfillable issue never spins forever (FR-017).
_MAX_REWORK = 3


class Orchestrator:
    """Runs the pipeline for one frozen brief as a background asyncio task."""

    def __init__(
        self,
        bus: EventBus,
        cancel: CancellationPort,
        store: StateStorePort,
        llm: LLMPort,
        search: SearchPort,
        retrieval: RetrievalPort,
        execution: CodeExecutionPort,
    ) -> None:
        self._bus = bus
        self._cancel = cancel
        self._store = store
        self._llm = llm
        self._search = search
        self._retrieval = retrieval
        self._execution = execution
        self._handlers = {
            "architect": self._plan,
            "scout": self._research,
            "data_analyst": self._analyze,
            "writer": self._write,
            "critic": self._review,
        }

    async def run(self, session_id: str, brief: dict) -> None:
        """Run the pipeline to completion, emitting events and snapshots.

        Args:
            session_id: The session being run.
            brief: The frozen ResearchBrief (pipeline input, read-only).
        """
        state = PipelineState(session_id=session_id, research_brief=brief, phase="plan")
        if not await self._try_snapshot(state, session_id):
            return
        self._bus.emit(session_id, PhaseEvent(phase="plan"))

        while state.phase != "done":
            if self._cancel.is_cancelled(session_id):
                await self._snapshot_quiet(state)
                self._bus.emit(session_id, DoneEvent(final_report_url="", status="cancelled"))
                return

            try:
                await self.run_phase(state)
            except Exception as exc:  # noqa: BLE001 — agent/LLM/search failure
                state.errors.append(
                    {"phase": state.phase, "error": type(exc).__name__, "message": str(exc)}
                )
                await self._snapshot_quiet(state)
                self._bus.emit(session_id, ErrorEvent(code="step_failed", message=str(exc)))
                return

            state.phase = self._advance(state)
            if not await self._try_snapshot(state, session_id):
                return
            if state.phase != "done":
                self._bus.emit(session_id, PhaseEvent(phase=state.phase))

        # Persist the final report to the reports table (durable, queryable).
        if state.final_report is not None:
            await self._store.save_report(session_id, state.final_report)
        self._bus.emit(session_id, DoneEvent(final_report_url=f"/research/{session_id}/report"))

    async def run_phase(self, state: PipelineState) -> None:
        """Dispatch one phase to its worker via the WORKERS policy table."""
        worker = WORKERS[state.phase]
        await self._handlers[worker](state)

    async def _plan(self, state: PipelineState) -> None:
        """Generate section plans from the frozen brief."""
        state.section_plans = await architect.legacy_plan(self._llm, state.research_brief)

    async def _research(self, state: PipelineState) -> None:
        """Gather evidence, sources, claims, observations, and per-section coverage."""
        for section in state.section_plans:
            result = await scout.research(section, self._search, self._retrieval, self._llm)
            state.evidence.update(result["evidence"])
            state.sources.update(result["sources"])
            state.claims.update(result["claims"])
            state.claim_evidence_links.extend(result["claim_evidence_links"])
            state.quantitative_observations.update(result["quantitative_observations"])
            coverage = result["section_coverage"]
            if coverage["section_id"]:
                state.section_coverage[coverage["section_id"]] = coverage
                if coverage["gaps"]:
                    fill = await scout.gap_fill(section, result["claims"], coverage, self._search)
                    state.evidence.update(fill["evidence"])
                    state.sources.update(fill["sources"])
        trace = await scout.citation_trace(state.sources, self._search)
        state.evidence.update(trace["evidence"])
        state.sources.update(trace["sources"])
        self._drain_gaps(self._search, "source_unavailable", state)
        self._drain_gaps(self._retrieval, "milvus_unavailable", state)

    def _drain_gaps(self, source: Any, code: str, state: PipelineState) -> None:
        """Drain a source's degradation events and emit a non-fatal error for unavailable ones."""
        take_gaps = getattr(source, "take_gaps", None)
        if take_gaps is None:
            return
        degraded = state.run_metadata.setdefault("degraded_sources", [])
        for gap in take_gaps():
            degraded.append(gap)
            if gap.get("reason") == "unavailable":
                self._bus.emit(
                    state.session_id,
                    ErrorEvent(
                        code=code,
                        message=f"{gap.get('source')} unavailable; degraded",
                    ),
                )

    async def _analyze(self, state: PipelineState) -> None:
        """Normalize metrics and run fixed analysis templates over compatible ones."""
        state.comparable_metrics = await data_analyst.analyze(
            state.quantitative_observations, self._llm
        )
        compatible = {
            k: v for k, v in state.comparable_metrics.items() if v["comparability"] == "compatible"
        }
        if compatible:
            artifact = await code_crafter.analyze(compatible, self._execution)
            state.analysis_artifacts[artifact["artifact_id"]] = artifact

    async def _write(self, state: PipelineState) -> None:
        """Write (or revise) draft sections and bindings."""
        if state.critic_feedback:
            result = await writer.revise_report(
                state.section_plans,
                state.claims,
                state.evidence,
                state.comparable_metrics,
                state.analysis_artifacts,
                state.research_brief,
                state.draft_sections,
                state.critic_feedback,
                self._llm,
            )
        else:
            result = await writer.write_report(
                state.section_plans,
                state.claims,
                state.evidence,
                state.comparable_metrics,
                state.analysis_artifacts,
                state.research_brief,
                self._llm,
            )
        state.draft_sections = result["draft_sections"]
        state.draft_claim_bindings = result["draft_claim_bindings"]
        state.final_report = result["final_report"]

    async def _review(self, state: PipelineState) -> None:
        """Review draft bindings; routing to the next phase is policy-driven."""
        state.critic_feedback = await critic.review(
            state.draft_claim_bindings, state.claims, state.evidence, state.sources, self._llm
        )

    def _advance(self, state: PipelineState) -> str:
        """Return the next phase, applying review rework routing with a cap."""
        if state.phase != "review":
            return next_phase(asdict(state))
        action = route_after_review(state.critic_feedback)
        if action == "done":
            return "done"
        rework_count = state.run_metadata.get("rework_count", 0)
        if rework_count >= _MAX_REWORK:
            state.run_metadata.setdefault("rework_limit", []).append({"action": action})
            return "done"
        state.run_metadata["rework_count"] = rework_count + 1
        target = phase_after_review(action)
        self._bus.emit(state.session_id, ReworkEvent(issue_id="", action=action, target=target))
        return target

    async def _try_snapshot(self, state: PipelineState, session_id: str) -> bool:
        """Snapshot the state; on persistence failure emit error and return False."""
        try:
            await self._store.save_snapshot(session_id, state.phase, asdict(state))
            return True
        except Exception as exc:  # noqa: BLE001 — PG down: truth source lost
            self._bus.emit(session_id, ErrorEvent(code="persistence_unavailable", message=str(exc)))
            return False

    async def _snapshot_quiet(self, state: PipelineState) -> None:
        """Best-effort snapshot; swallow persistence failure."""
        try:
            await self._store.save_snapshot(state.session_id, state.phase, asdict(state))
        except Exception:  # noqa: BLE001, S110 — best-effort snapshot may fail
            pass


class RunUnitCoordinator:
    """Formal unit commits; the pre-mono Orchestrator above is isolated until cutover.

    An explicit executor/context factory is required. This boundary does not
    choose Machine routes or create Reports; those are separately committed by
    the Run driver after all required units have completed.
    """

    def __init__(
        self,
        *,
        store,
        cache,
        executor: PhaseExecutor,
        clock,
        elapsed_s: Callable[[], float],
        committed: Callable[[UnitCommit], None],
    ):
        self.store, self.cache, self.executor, self.clock = store, cache, executor, clock
        self.elapsed_s, self.committed = elapsed_s, committed

    async def _load_owned(self, claimed):
        claimed = ClaimedRun.model_validate(claimed)
        async with self.store.transaction() as tx:
            run = await self.store.research.check_run_lease(claimed, tx)
            point = await self.store.research.load_latest_checkpoint(
                claimed.owner_id, run.run_id, tx
            )
        if point is None or point.seq != run.checkpoint_seq or point.phase != run.phase:
            raise AppError("invalid_state", "Run does not point to a complete current checkpoint")
        if (
            point.state.brief_hash != run.brief_hash
            or point.state.run_metadata.config != run.config_snapshot
        ):
            raise AppError(
                "config_unavailable", "Checkpoint differs from frozen execution configuration"
            )
        return ClaimedRun(owner_id=claimed.owner_id, run=run), point

    async def execute_unit(self, claimed, unit, context_factory):
        """Execute or verify/skip one trusted unit; phase stays unchanged."""
        unit = UnitScope.model_validate_json(unit.model_dump_json())
        claimed, point = await self._load_owned(claimed)
        previous = point.state.run_metadata.unit_manifest.get(unit.unit_id)
        if previous is not None:
            await self._verify_completed(claimed, point, unit, previous)
            return UnitCommit(claimed=claimed, checkpoint=point, skipped=True)
        if claimed.run.status != "running" or claimed.run.cancel_requested_at is not None:
            raise AppError("invalid_session_state", "Execution is stopping")
        if unit.phase != point.phase:
            raise AppError("invalid_state", "Unit differs from the next pending phase")
        if unit.phase == "plan" and set(unit.section_ids) != {f"section_{n}" for n in range(1, 6)}:
            raise AppError("invalid_state", "Planning unit must cover the complete plan")
        requirements = {
            requirement.requirement_id
            for plan in point.state.section_plans
            if plan.section_id in unit.section_ids
            for requirement in plan.analysis_requirements
        }
        if not set(unit.requirement_ids) <= requirements:
            raise AppError("invalid_state", "Unit analysis requirements are outside its sections")
        value = PhaseInput.from_state(point.state)
        context = ExecutionContext.model_validate(context_factory(claimed, point, unit, value))
        if (
            context.owner_id != claimed.owner_id
            or context.run_id != claimed.run.run_id
            or context.lease_token != claimed.run.lease_token
            or context.unit != unit
            or context.config != claimed.run.config_snapshot
        ):
            raise AppError("invalid_state", "Context differs from the coordinator's owned unit")
        result = await self.executor.execute_phase(value, context)
        try:
            merged = merge_phase_result(
                point.state,
                result,
                target_sections=unit.section_ids,
                target_requirements=unit.requirement_ids or None,
            )
        except (ValueError, TypeError):
            raise AppError(
                "invalid_state", "Worker result cannot be merged into its scoped unit"
            ) from None
        envelope = UnitEnvelope(
            schema_version=1, scope=unit, input_hash=value.semantic_hash, result=result
        )
        body = json.dumps(
            envelope.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        reference = await self.cache.put(
            f"phase-results/{claimed.run.run_id}", body, "application/json"
        )
        if (
            reference.sha256 != canonical_hash(envelope)
            or reference.key != f"phase-results/{claimed.run.run_id}/{reference.sha256}"
            or reference.size != len(body)
            or reference.media_type != "application/json"
        ):
            raise AppError("invalid_state", "Unit cache returned an inconsistent content reference")
        now = self.clock.now_utc()
        entry = UnitResult(
            unit_id=unit.unit_id,
            phase=unit.phase,
            input_hash=value.semantic_hash,
            result_hash=reference.sha256,
            result_ref=reference,
            checkpoint_seq=point.seq + 1,
            completed_at=now,
            affected_ids=sorted([*unit.section_ids, *unit.requirement_ids]),
        )
        # No external I/O while holding the Run lock. Ledger read and complete
        # snapshot publication share one transaction and expected sequence.
        async with self.store.transaction() as tx:
            budget = await self.store.research.load_tool_budget(
                claimed.owner_id, claimed.run.run_id, tx
            )
            if budget is not None and any(
                getattr(budget.pending, name)
                for name in ("llm_calls", "search_calls", "fetch_calls", "tokens")
            ):
                raise AppError("invalid_state", "Unit still has outstanding tool reservations")
            used = budget.used if budget is not None else point.state.run_metadata.budget_used
            used = BudgetUsage.model_validate(
                used.model_dump()
                | {
                    "elapsed_s": max(
                        used.elapsed_s,
                        point.state.run_metadata.budget_used.elapsed_s,
                        self.elapsed_s(),
                    )
                }
            )
            metadata = merged.run_metadata.model_dump() | {
                "budget_used": used,
                "unit_manifest": merged.run_metadata.unit_manifest | {unit.unit_id: entry},
            }
            state = MonoState.model_validate(merged.model_dump() | {"run_metadata": metadata})
            candidate = Checkpoint(
                snapshot_id=uuid4(),
                run_id=state.run_id,
                seq=point.seq + 1,
                schema_version=1,
                phase=state.phase,
                state=state,
                state_hash=canonical_hash(state),
                created_at=now,
            )
            newer = await self.store.research.commit_checkpoint(claimed, point.seq, candidate, tx)
        committed = UnitCommit(claimed=newer, checkpoint=candidate, skipped=False)
        try:
            self.committed(committed)  # Projection only AFTER the authoritative commit.
        except Exception:  # noqa: BLE001 -- ephemeral projection is not a persisted Run failure
            logging.getLogger(__name__).warning("unit_projection_failed")
        return committed

    async def _verify_completed(self, claimed, latest, scope, entry):
        if entry.phase != scope.phase or entry.checkpoint_seq > latest.seq:
            raise AppError("invalid_state", "Completed unit has a conflicting checkpoint identity")
        body = await self.cache.read(entry.result_ref)
        try:
            envelope = UnitEnvelope.model_validate_json(body)
            if (
                canonical_hash(envelope) != entry.result_hash
                or envelope.scope != scope
                or envelope.input_hash != entry.input_hash
            ):
                raise ValueError("Unit object identity differs")
        except (ValueError, TypeError):
            raise AppError("invalid_state", "Persisted unit object is invalid") from None
        async with self.store.transaction() as tx:
            current = await self.store.research.check_run_lease(claimed, tx)
            if current.checkpoint_seq != latest.seq:
                raise AppError("stale_resource", "Checkpoint changed during unit verification")
            before = await self.store.research.load_checkpoint(
                claimed.owner_id, claimed.run.run_id, entry.checkpoint_seq - 1, tx
            )
            after = await self.store.research.load_checkpoint(
                claimed.owner_id, claimed.run.run_id, entry.checkpoint_seq, tx
            )
        try:
            if (
                before is None
                or after is None
                or after.state.run_metadata.unit_manifest.get(scope.unit_id) != entry
                or PhaseInput.from_state(before.state).semantic_hash != envelope.input_hash
            ):
                raise ValueError("Unit checkpoints do not match its input")
            reconstructed = merge_phase_result(
                before.state,
                envelope.result,
                target_sections=scope.section_ids,
                target_requirements=scope.requirement_ids or None,
            )
            expected_metadata = reconstructed.run_metadata.model_dump() | {
                "budget_used": after.state.run_metadata.budget_used,
                "unit_manifest": before.state.run_metadata.unit_manifest | {scope.unit_id: entry},
            }
            expected = MonoState.model_validate(
                reconstructed.model_dump() | {"run_metadata": expected_metadata}
            )
            if expected != after.state:
                raise ValueError("Unit result differs from committed facts")
        except (ValueError, TypeError):
            raise AppError(
                "invalid_state", "Completed unit cannot be verified against its checkpoints"
            ) from None
