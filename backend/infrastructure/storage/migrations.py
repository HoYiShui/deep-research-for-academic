"""Numbered SQL migrations: apply migrations/*.sql in version order.

A schema_migrations table tracks applied versions; each migration runs inside
a single transaction (atomic). Files are named ``NNNN_description.sql`` and
applied in filename order. New schema changes add the next numbered file —
no ORM, no Alembic.
"""

from __future__ import annotations

from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(pool) -> None:
    """Apply any unapplied migrations in filename order."""
    await pool.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY,"
        " applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        done = await pool.fetchval("SELECT 1 FROM schema_migrations WHERE version = $1", version)
        if done:
            continue
        sql = path.read_text()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
