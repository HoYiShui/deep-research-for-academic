"""PostgreSQL state store adapter (sessions + phase snapshots)."""

from __future__ import annotations

import json
import os

import asyncpg


class PostgresStateStore:
    """StateStorePort implementation backed by PostgreSQL."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn)
        return self._pool

    async def save_session(self, session_id: str, state: dict) -> None:
        pool = await self._get_pool()
        await pool.execute(
            "INSERT INTO sessions (session_id, state) VALUES ($1, $2) "
            "ON CONFLICT (session_id) DO UPDATE SET state = $2",
            session_id,
            json.dumps(state),
        )

    async def load_session(self, session_id: str) -> dict | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT state FROM sessions WHERE session_id = $1", session_id)
        return json.loads(row["state"]) if row else None

    async def save_snapshot(self, session_id: str, phase: str, state: dict) -> None:
        pool = await self._get_pool()
        await pool.execute(
            "INSERT INTO phase_snapshots (session_id, phase, state) VALUES ($1, $2, $3)",
            session_id,
            phase,
            json.dumps(state),
        )

    async def load_latest_snapshot(self, session_id: str, phase: str) -> dict | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT state FROM phase_snapshots WHERE session_id = $1 AND phase = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            session_id,
            phase,
        )
        return json.loads(row["state"]) if row else None
