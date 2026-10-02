"""Contract tests for PostgresStateStore (mocked pool; SQL roundtrips)."""

import json

import pytest

from infrastructure.storage.postgres import PostgresStateStore


class _FakePool:
    """Simulates the asyncpg pool over in-memory tables for SQL roundtrips."""

    def __init__(self) -> None:
        self._briefs: dict[str, dict] = {}
        self._snapshots: list[tuple[str, str, dict]] = []

    async def execute(self, query, *args):
        if "briefs" in query:
            self._briefs[args[0]] = {"brief": json.loads(args[2]), "task_type": args[1]}
        elif "phase_snapshots" in query:
            self._snapshots.append((args[0], args[1], json.loads(args[2])))

    async def fetchrow(self, query, *args):
        if "briefs" in query:
            rec = self._briefs.get(args[0])
            return {"brief": json.dumps(rec["brief"]), "task_type": rec["task_type"]} if rec else None
        if "phase_snapshots" in query:
            for sid, phase, state in reversed(self._snapshots):
                if sid == args[0] and phase == args[1]:
                    return {"state": json.dumps(state)}
            return None
        return None

    async def fetchval(self, query, *args):
        return None

    async def fetch(self, query, *args):
        return []


@pytest.mark.asyncio
async def test_postgres_brief_roundtrip() -> None:
    store = PostgresStateStore()
    store._pool = _FakePool()
    await store.save_brief("s1", {"query": "x"}, task_type="idea_exploration")
    assert await store.load_brief("s1") == {"query": "x", "task_type": "idea_exploration"}


@pytest.mark.asyncio
async def test_postgres_snapshot_latest_roundtrip() -> None:
    store = PostgresStateStore()
    store._pool = _FakePool()
    await store.save_snapshot("s1", "research", {"v": 1})
    await store.save_snapshot("s1", "research", {"v": 2})
    assert await store.load_latest_snapshot("s1", "research") == {"v": 2}
