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
    """StateStorePort fake: in-memory dicts per stable table."""

    def __init__(self) -> None:
        self._status: dict[str, str] = {}
        self._messages: dict[str, list[dict]] = {}
        self._briefs: dict[str, dict] = {}
        self._snapshots: list[tuple[str, str, dict]] = []
        self._reports: dict[str, dict] = {}

    async def create_session(self, session_id: str, status: str = "clarify") -> None:
        self._status[session_id] = status

    async def set_session_status(self, session_id: str, status: str) -> None:
        self._status[session_id] = status

    async def get_session_status(self, session_id: str) -> str | None:
        return self._status.get(session_id)

    async def append_message(self, session_id: str, role: str, content: str) -> None:
        self._messages.setdefault(session_id, []).append({"role": role, "content": content})

    async def list_messages(self, session_id: str) -> list[dict]:
        return list(self._messages.get(session_id, []))

    async def save_brief(self, session_id: str, brief: dict, task_type: str = "") -> None:
        stored = dict(brief)
        if task_type:
            stored["task_type"] = task_type
        self._briefs[session_id] = stored

    async def load_brief(self, session_id: str) -> dict | None:
        return dict(self._briefs[session_id]) if session_id in self._briefs else None

    async def save_snapshot(self, session_id: str, phase: str, state: dict) -> None:
        self._snapshots.append((session_id, phase, state))

    async def load_latest_snapshot(self, session_id: str, phase: str) -> dict | None:
        for sid, ph, st in reversed(self._snapshots):
            if sid == session_id and ph == phase:
                return st
        return None

    async def save_report(self, session_id: str, content: dict) -> None:
        self._reports[session_id] = content

    async def load_report(self, session_id: str) -> dict | None:
        return self._reports.get(session_id)


class FakeRetrieval:
    """RetrievalPort fake: returns no local-KB chunks."""

    async def retrieve(self, query: str, kb_id: str, top_k: int) -> list:
        return []


class FakeExecution:
    """CodeExecutionPort fake: returns a fixed result."""

    async def execute(self, code: str, input_data: dict, timeout_s: int = 30) -> dict:
        return {"result": "ok"}
