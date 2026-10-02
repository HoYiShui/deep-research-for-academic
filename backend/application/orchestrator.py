"""Pipeline loop: drives the explicit state machine and emits events.

The orchestrator is the sole control flow. It runs plan -> research -> analyze
-> write -> review (with rework routing via the policy table), snapshots the
SSOT at every phase boundary, and weaves in the per-dependency failure
semantics (LLM exhaustion terminates; persistence failure terminates; search
and Milvus degrade without terminating).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from application.ports import CancellationPort, StateStorePort
from application.sse import EventBus
from domain.ports import CodeExecutionPort, LLMPort, RetrievalPort, SearchPort
from domain.research.agents import architect, code_crafter, critic, data_analyst, scout, writer
from domain.research.events import DoneEvent, ErrorEvent, PhaseEvent, ReworkEvent
from domain.research.machine import next_phase, phase_after_review, route_after_review
from domain.research.state import PipelineState

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

    async def run(self, session_id: str, brief: dict) -> None:
        """Run the pipeline to completion, emitting events and snapshots.

        Args:
            session_id: The session being run.
            brief: The frozen ResearchBrief (pipeline input, read-only).
        """
        state = PipelineState(session_id=session_id, brief=brief, phase="plan")
        if not await self._try_snapshot(state, session_id):
            return
        self._bus.emit(session_id, PhaseEvent(phase="plan"))

        while state.phase != "done":
            if self._cancel.is_cancelled(session_id):
                await self._snapshot_quiet(state)
                self._bus.emit(session_id, DoneEvent(final_report_url="", status="cancelled"))
                return

            try:
                await self._run_phase(state)
            except Exception as exc:  # noqa: BLE001 — agent/LLM/search failure
                state.errors.append({"phase": state.phase, "message": str(exc)})
                await self._snapshot_quiet(state)
                self._bus.emit(session_id, ErrorEvent(code="step_failed", message=str(exc)))
                return

            state.phase = self._advance(state)
            if not await self._try_snapshot(state, session_id):
                return
            if state.phase != "done":
                self._bus.emit(session_id, PhaseEvent(phase=state.phase))

        self._bus.emit(session_id, DoneEvent(final_report_url=f"/research/{session_id}/report"))

    async def _run_phase(self, state: PipelineState) -> None:
        """Dispatch one phase to its worker(s)."""
        if state.phase == "plan":
            state.section_plans = await architect.plan(self._llm, state.brief)
        elif state.phase == "research":
            await self._research(state)
        elif state.phase == "analyze":
            await self._analyze(state)
        elif state.phase == "write":
            self._write(state)
        elif state.phase == "review":
            self._review(state)

    async def _research(self, state: PipelineState) -> None:
        """Gather evidence per section, dedup, and collect coverage gaps."""
        for section in state.section_plans:
            result = await scout.research(section, self._search, self._retrieval)
            state.evidence.extend(result["evidence"])
        state.evidence = _dedup_evidence(state.evidence)
        self._drain_gaps(self._search, "source_unavailable", state)
        self._drain_gaps(self._retrieval, "milvus_unavailable", state)

    def _drain_gaps(self, source: Any, code: str, state: PipelineState) -> None:
        """Drain a source's coverage gaps and emit a non-fatal error for degraded ones."""
        take_gaps = getattr(source, "take_gaps", None)
        if take_gaps is None:
            return
        for gap in take_gaps():
            state.coverage_gaps.append(gap)
            if gap.get("reason") == "unavailable":
                self._bus.emit(
                    state.session_id,
                    ErrorEvent(
                        code=code,
                        message=f"{gap.get('source')} unavailable; degraded",
                    ),
                )

    async def _analyze(self, state: PipelineState) -> None:
        """Normalize metrics and run fixed analysis templates over them."""
        state.comparable_metrics = data_analyst.analyze([])
        for metric in state.comparable_metrics:
            artifact = await code_crafter.analyze([metric], self._execution)
            state.analysis_artifacts.append(artifact)

    def _write(self, state: PipelineState) -> None:
        """Write draft sections and bindings; mark the draft as final report."""
        result = writer.write_report(state.section_plans, state.evidence)
        state.draft_sections = result["draft_sections"]
        state.draft_claim_bindings = result["draft_claim_bindings"]
        state.final_report = {"sections": state.draft_sections}

    def _review(self, state: PipelineState) -> None:
        """Review draft bindings; routing to the next phase is policy-driven."""
        state.critic_feedback = critic.review(state.draft_claim_bindings, state.evidence)

    def _advance(self, state: PipelineState) -> str:
        """Return the next phase, applying review rework routing with a cap."""
        if state.phase != "review":
            return next_phase(asdict(state))
        action = route_after_review(state.critic_feedback)
        if action == "done":
            return "done"
        rework_count = state.run_metadata.get("rework_count", 0)
        if rework_count >= _MAX_REWORK:
            state.coverage_gaps.append({"reason": "rework_limit", "action": action})
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


def _dedup_evidence(evidence: list[dict]) -> list[dict]:
    """Drop duplicate evidence by (source_id, location, quote)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for item in evidence:
        key = (item.get("source_id"), item.get("location"), item.get("quote"))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
