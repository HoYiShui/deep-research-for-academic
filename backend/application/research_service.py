"""Entry use case: create a session and spawn the pipeline."""

from __future__ import annotations

import asyncio
import uuid

from application.orchestrator import Orchestrator
from application.session_service import SessionService


class ResearchService:
    """Mediates session_service (clarify) and orchestrator (pipeline)."""

    def __init__(self, sessions: SessionService, orchestrator: Orchestrator) -> None:
        self._sessions = sessions
        self._orchestrator = orchestrator

    async def start(self) -> dict:
        """Create a session; clarify runs later via POST /messages."""
        session_id = uuid.uuid4().hex
        await self._sessions.create(session_id)
        return {"session_id": session_id, "status": "clarify"}

    def spawn_pipeline(self, session_id: str, brief: dict) -> asyncio.Task:
        """Spawn the pipeline as a background task (fire-and-forget)."""
        return asyncio.create_task(self._orchestrator.run(session_id, brief))
