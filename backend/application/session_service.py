"""Clarify loop: interactive multi-turn, one round per POST /messages."""

from __future__ import annotations

import asyncio

from application.ports import StateStorePort
from domain.ports import LLMPort
from domain.research.agents import architect
from domain.research.machine import decide_status

# Cap on clarify rounds; past this the brief is frozen with conservative defaults (FR-003).
MAX_CLARIFY_ROUNDS = 3


class SessionService:
    """Owns the clarify loop, persisting to sessions/messages/briefs tables."""

    def __init__(self, llm: LLMPort, store: StateStorePort) -> None:
        self._llm = llm
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}

    async def create(self, session_id: str, query: str = "") -> dict:
        """Create a new session and seed the initial research request.

        Args:
            session_id: The session to create.
            query: The initial research request, seeded into the brief draft.
        """
        await self._store.create_session(session_id, "clarify")
        if query:
            await self._store.save_brief(session_id, {"query": query})
        return {"session_id": session_id, "status": "clarify"}

    async def clarify_round(self, session_id: str, answer: str) -> dict:
        """Advance one clarify round, serialized per session.

        Args:
            session_id: The session to advance.
            answer: The user's answer to the previous question (or the query).

        Returns:
            {"status": "ask" | "ready", "questions": [...]}.
        """
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            brief = await self._store.load_brief(session_id) or {}
            judgment = await architect.clarify(self._llm, brief, answer)
            brief.update(judgment.get("brief_patch", {}))

            messages = await self._store.list_messages(session_id)
            if len(messages) >= MAX_CLARIFY_ROUNDS:
                status = "ready"  # conservative default once the round cap is reached
            else:
                status = decide_status(judgment.get("missing_fields", []))
            await self._store.append_message(session_id, "user", answer)
            await self._store.save_brief(session_id, brief, brief.get("task_type", ""))
            await self._store.set_session_status(session_id, status)

            return {
                "status": status,
                "questions": judgment.get("questions", []),
                "brief": brief,
            }
