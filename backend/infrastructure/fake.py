"""Fake adapters for the walking skeleton.

Each fake satisfies its port contract with fixed, deterministic data so the
skeleton can run end-to-end before any real adapter exists.
"""
from __future__ import annotations

from domain.ports import SearchResult


class FakeLLM:
    """LLMPort fake: returns a fixed string."""

    def __init__(self, response: str = "{}") -> None:
        self._response = response

    async def complete(self, prompt: str) -> str:
        return self._response


class FakeSearch:
    """SearchPort fake: returns one fixed candidate."""

    async def search(self, query: str) -> list[SearchResult]:
        return [
            SearchResult(
                source_id="fake-1",
                source_type="paper",
                title="Fake paper",
                snippet="A fake snippet for the skeleton.",
                url="https://example.com",
            )
        ]


class FakeStateStore:
    """StateStorePort fake: in-memory dict."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}
        self._snapshots: list[tuple[str, str, dict]] = []

    async def save_session(self, session_id: str, state: dict) -> None:
        self._sessions[session_id] = state

    async def load_session(self, session_id: str) -> dict | None:
        return self._sessions.get(session_id)

    async def save_snapshot(self, session_id: str, phase: str, state: dict) -> None:
        self._snapshots.append((session_id, phase, state))

    async def load_latest_snapshot(self, session_id: str, phase: str) -> dict | None:
        for sid, ph, st in reversed(self._snapshots):
            if sid == session_id and ph == phase:
                return st
        return None


class FakeRetrieval:
    """RetrievalPort fake: returns no local-KB chunks."""

    async def retrieve(self, query: str, kb_id: str, top_k: int) -> list:
        return []


class FakeExecution:
    """CodeExecutionPort fake: returns a fixed result."""

    async def execute(self, code: str, input_data: dict, timeout_s: int = 30) -> dict:
        return {"result": "ok"}
