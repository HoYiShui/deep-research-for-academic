"""Entry use case: create a session, spawn the pipeline, and read back results."""

from __future__ import annotations

import asyncio
import uuid

from application.orchestrator import Orchestrator
from application.ports import CancellationPort, StateStorePort
from application.session_service import SessionService

# Pipeline phases, in order, for recovering the latest phase from snapshots.
_PHASES = ["plan", "research", "analyze", "write", "review", "done"]


class ResearchService:
    """Mediates session_service (clarify) and orchestrator (pipeline)."""

    def __init__(
        self,
        sessions: SessionService,
        orchestrator: Orchestrator,
        store: StateStorePort,
        cancel: CancellationPort,
    ) -> None:
        self._sessions = sessions
        self._orchestrator = orchestrator
        self._store = store
        self._cancel = cancel

    async def start(self, query: str = "") -> dict:
        """Create a session; clarify runs later via POST /messages.

        Args:
            query: The initial research request, seeded into the session brief.
        """
        session_id = uuid.uuid4().hex
        await self._sessions.create(session_id, query)
        return {"session_id": session_id, "status": "clarify"}

    def spawn_pipeline(self, session_id: str, brief: dict) -> asyncio.Task:
        """Spawn the pipeline as a background task (fire-and-forget)."""
        return asyncio.create_task(self._orchestrator.run(session_id, brief))

    def cancel(self, session_id: str) -> None:
        """Request cancellation; the orchestrator stops at the next phase boundary."""
        self._cancel.set_cancelled(session_id)

    async def get_report(self, session_id: str) -> dict | None:
        """Return the final report from the completed pipeline, if present."""
        snapshot = await self._store.load_latest_snapshot(session_id, "done")
        return snapshot.get("final_report") if snapshot else None

    async def get_status(self, session_id: str) -> dict:
        """Return the current status by recovering the latest phase snapshot."""
        for phase in reversed(_PHASES):
            snapshot = await self._store.load_latest_snapshot(session_id, phase)
            if snapshot is not None:
                return {"session_id": session_id, "status": "done" if phase == "done" else "running",
                        "phase": phase}
        session = await self._store.load_session(session_id)
        return {"session_id": session_id, "status": (session or {}).get("status", "clarify")}
