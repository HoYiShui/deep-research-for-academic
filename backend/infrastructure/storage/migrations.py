"""Numbered SQL migrations: apply migrations/*.sql in version order.

A schema_migrations table tracks applied versions; each migration runs inside
a single transaction (atomic). Files are named ``NNNN_description.sql`` and
applied in filename order. New schema changes add the next numbered file —
no ORM, no Alembic.
"""

from __future__ import annotations

from pathlib import Path

from infrastructure.storage.legacy_backup import LEGACY_TABLES, backup_legacy_tables

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(
    pool,
    *,
    migrations_dir: Path | None = None,
    through_version: str | None = None,
    backup_dir: Path | None = None,
) -> None:
    """Serialize migrations across processes, atomically applying a pending batch.

    The lock precedes even migration-table creation. Database-scoped advisory
    transaction locks are released on failure/cancellation/process death. Old
    callers may pin 0001 but must never write a newer incompatible schema.
    """
    paths = sorted((migrations_dir or _MIGRATIONS_DIR).glob("*.sql"))
    if not paths:
        raise ValueError("Migration directory contains no SQL files")
    versions = [path.stem for path in paths]
    if through_version is not None:
        if through_version not in versions:
            raise ValueError("Unknown migration boundary")
        paths = paths[: versions.index(through_version) + 1]
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", 0x44523441)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
        applied = set(
            await conn.fetchval(
                "SELECT coalesce(array_agg(version), ARRAY[]::text[]) FROM schema_migrations"
            )
        )
        supported = {path.stem for path in paths}
        if applied - supported:
            raise RuntimeError("Database schema is newer or unknown to this runtime")
        for path in paths:
            version = path.stem
            if version in applied:
                continue
            if version == "0002_mono_research":
                tables = ",".join('public."' + name + '"' for name in LEGACY_TABLES)
                await conn.execute("LOCK TABLE " + tables + " IN SHARE MODE")
                has_rows = False
                for name in LEGACY_TABLES:
                    if await conn.fetchval(f'SELECT EXISTS(SELECT 1 FROM public."{name}")'):
                        has_rows = True
                        break
                if has_rows:
                    if backup_dir is None:
                        raise RuntimeError(
                            "Legacy upgrade requires an explicit local backup directory"
                        )
                    await backup_legacy_tables(conn, backup_dir)
            sql = path.read_text(encoding="utf-8")
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
