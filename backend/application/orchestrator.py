"""Pipeline loop: autonomous, drives machine.next_phase and emits events."""

from __future__ import annotations

import asyncio

from application.ports import CancellationPort
from application.sse import EventBus
from domain.research.events import DoneEvent, PhaseEvent
from domain.research.machine import next_phase


class Orchestrator:
    """Runs the pipeline for one frozen brief as a background asyncio task."""

    def __init__(self, bus: EventBus, cancel: CancellationPort) -> None:
        self._bus = bus
        self._cancel = cancel

    async def run(self, session_id: str, brief: dict) -> None:
        """Run the pipeline loop, emitting a PhaseEvent per phase.

        Args:
            session_id: The session being run.
            brief: The frozen ResearchBrief (pipeline input, read-only).
        """
        state = {"session_id": session_id, "phase": "plan", "brief": brief}
        while state["phase"] != "done":
            if self._cancel.is_cancelled(session_id):
                break
            state["phase"] = next_phase(state)
            self._bus.emit(session_id, PhaseEvent(phase=state["phase"]))
            await asyncio.sleep(0.05)  # fake per-phase work; real agents in S1+
        self._bus.emit(session_id, DoneEvent(report_url=f"/research/{session_id}/report"))
