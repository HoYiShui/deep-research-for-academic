"""PostgreSQL state store: sessions/messages/briefs/reports/phase_snapshots.

Maps each StateStorePort method to its stable table; messages are append-only,
briefs/reports are versioned (V1 keeps version 1, load takes the latest).
"""

from __future__ import annotations

import json
import os

import asyncpg

from infrastructure.storage.migrations import run_migrations


class PostgresStateStore:
    """StateStorePort implementation backed by PostgreSQL."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn)
            await run_migrations(self._pool)
        return self._pool

    # ---- sessions ----
    async def create_session(self, session_id: str, status: str = "clarify") -> None:
        pool = await self._get_pool()
        await pool.execute(
            "INSERT INTO sessions (session_id, status) VALUES ($1, $2)", session_id, status
        )

    async def set_session_status(self, session_id: str, status: str) -> None:
        pool = await self._get_pool()
        await pool.execute(
            "UPDATE sessions SET status = $2, updated_at = now() WHERE session_id = $1",
            session_id,
            status,
        )

    async def get_session_status(self, session_id: str) -> str | None:
        pool = await self._get_pool()
        return await pool.fetchval("SELECT status FROM sessions WHERE session_id = $1", session_id)

    # ---- messages (append-only) ----
    async def append_message(self, session_id: str, role: str, content: str) -> None:
        pool = await self._get_pool()
        await pool.execute(
            "INSERT INTO messages (session_id, role, content) VALUES ($1, $2, $3)",
            session_id,
            role,
            content,
        )

    async def list_messages(self, session_id: str) -> list[dict]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "SELECT role, content FROM messages WHERE session_id = $1 ORDER BY created_at",
            session_id,
        )
        return [dict(row) for row in rows]

    # ---- brief (versioned; V1 keeps version 1) ----
    async def save_brief(self, session_id: str, brief: dict, task_type: str = "") -> None:
        pool = await self._get_pool()
        await pool.execute(
            "INSERT INTO briefs (session_id, task_type, brief, version) VALUES ($1, $2, $3, 1) "
            "ON CONFLICT (session_id, version) DO UPDATE SET task_type = $2, brief = $3",
            session_id,
            task_type,
            json.dumps(brief),
        )

    async def load_brief(self, session_id: str) -> dict | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT task_type, brief FROM briefs WHERE session_id = $1 "
            "ORDER BY version DESC LIMIT 1",
            session_id,
        )
        if row is None:
            return None
        brief = json.loads(row["brief"])
        brief.setdefault("task_type", row["task_type"] or "")
        return brief

    # ---- snapshots ----
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

    # ---- report (versioned) ----
    async def save_report(self, session_id: str, content: dict) -> None:
        pool = await self._get_pool()
        await pool.execute(
            "INSERT INTO reports (session_id, content, version) VALUES ($1, $2, 1) "
            "ON CONFLICT (session_id, version) DO UPDATE SET content = $2",
            session_id,
            json.dumps(content),
        )

    async def load_report(self, session_id: str) -> dict | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT content FROM reports WHERE session_id = $1 ORDER BY version DESC LIMIT 1",
            session_id,
        )
        return json.loads(row["content"]) if row else None
