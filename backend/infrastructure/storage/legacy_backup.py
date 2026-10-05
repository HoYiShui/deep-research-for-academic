"""Consistent local backup of the six tables affected by the mono upgrade.

This is a migration safety export, not the PG+MinIO restore procedure in T058.
The caller holds SHARE locks on every affected table in the same transaction.
Private rows are never logged; exports are mode 0600 in a mode 0700 directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

LEGACY_TABLES = ("users", "sessions", "messages", "briefs", "reports", "phase_snapshots")


async def backup_legacy_tables(conn, destination: Path) -> tuple[Path, str]:
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix="mono-upgrade-", dir=destination))
    partial = directory / "legacy-backup.partial"
    completed = directory / "legacy-backup.jsonl"
    digest = hashlib.sha256()
    counts = {}
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:

        def write(record):
            encoded = (
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            output.write(encoded)
            digest.update(encoded)

        write(
            {
                "record_type": "header",
                "format": "dr4a-legacy-backup-v1",
                "database": await conn.fetchval("SELECT current_database()"),
                "schema_migrations": [
                    row["version"]
                    for row in await conn.fetch(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ],
            }
        )
        for name in LEGACY_TABLES:
            columns = [
                dict(row)
                for row in await conn.fetch(
                    "SELECT column_name,data_type,udt_name,is_nullable,column_default "
                    "FROM information_schema.columns WHERE table_schema='public' AND table_name=$1 "
                    "ORDER BY ordinal_position",
                    name,
                )
            ]
            constraints = [
                dict(row)
                for row in await conn.fetch(
                    "SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                    "WHERE conrelid=$1::regclass ORDER BY conname",
                    "public." + name,
                )
            ]
            indexes = [
                dict(row)
                for row in await conn.fetch(
                    "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=$1 "
                    "ORDER BY indexname",
                    name,
                )
            ]
            write(
                {
                    "record_type": "schema",
                    "table": name,
                    "columns": columns,
                    "constraints": constraints,
                    "indexes": indexes,
                }
            )
            count = 0
            # Table names are a constant allowlist, not caller-provided SQL.
            async for row in conn.cursor(
                f'SELECT to_jsonb(t) AS row FROM public."{name}" t ORDER BY 1', prefetch=100
            ):
                write({"record_type": "row", "table": name, "row": json.loads(row["row"])})
                count += 1
            counts[name] = count
        write({"record_type": "footer", "counts": counts, "payload_sha256": digest.hexdigest()})
        output.flush()
        os.fsync(output.fileno())

    verified = hashlib.sha256()
    with partial.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            verified.update(chunk)
    if verified.hexdigest() != digest.hexdigest():
        raise RuntimeError("Legacy backup read-back hash verification failed")
    partial.rename(completed)
    checksum = directory / "legacy-backup.sha256"
    checksum_fd = os.open(checksum, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(checksum_fd, "w", encoding="utf-8") as output:
        output.write(digest.hexdigest() + "\n")
        output.flush()
        os.fsync(output.fileno())
    for path in (directory, destination):
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return completed, digest.hexdigest()
