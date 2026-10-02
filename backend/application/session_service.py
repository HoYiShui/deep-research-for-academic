"""Clarify loop: interactive multi-turn, one round per POST /messages."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from application.ports import StateStorePort
from domain.ports import LLMPort
from domain.research.agents.base import call_llm, parse_json
from domain.research.machine import decide_status
from domain.research.state import SessionState


class SessionService:
    """Owns SessionState and drives the clarify loop (per-session serialized)."""

    def __init__(self, llm: LLMPort, store: StateStorePort) -> None:
        self._llm = llm
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}

    async def create(self, session_id: str) -> dict:
        """Create a new SessionState and persist it."""
        state = SessionState(session_id=session_id)
        await self._store.save_session(session_id, asdict(state))
        return asdict(state)

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
            raw = await self._store.load_session(session_id)
            state = SessionState(**raw) if raw else SessionState(session_id=session_id)

            # Real architect.clarify arrives in S1; here we call the LLM directly.
            judgment = parse_json(await call_llm(self._llm, answer))
            state.brief_draft.update(judgment.get("brief_patch", {}))
            state.clarification_history.append({"answer": answer})

            state.status = decide_status(judgment.get("missing_fields", []))
            await self._store.save_session(session_id, asdict(state))

            return {
                "status": state.status,
                "questions": judgment.get("questions", []),
                "brief": state.brief_draft,
            }
