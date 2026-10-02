"""Contract test for StateStorePort (PostgreSQL, mocked pool)."""

import json

import pytest

from infrastructure.storage.postgres import PostgresStateStore


class _FakePool:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}
        self._snapshots: list[tuple[str, str, str]] = []

    async def execute(self, query, *args):
        if "sessions" in query:
            self._sessions[args[0]] = args[1]

    async def fetchrow(self, query, *args):
        if "sessions" in query:
            raw = self._sessions.get(args[0])
            return {"state": raw} if raw else None
        if "phase_snapshots" in query:
            for sid, phase, state in reversed(self._snapshots):
                if sid == args[0] and phase == args[1]:
                    return {"state": state}
            return None
        return None


@pytest.mark.asyncio
async def test_postgres_save_and_load_session() -> None:
    store = PostgresStateStore()
    store._pool = _FakePool()
    await store.save_session("s1", {"phase": "plan"})
    assert await store.load_session("s1") == {"phase": "plan"}
