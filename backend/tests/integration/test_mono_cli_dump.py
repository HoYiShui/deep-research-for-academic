"""Actual CLI subprocess over isolated PG: newest seq, ownership, no writes."""

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from application.settings import Settings
from cli.commands.doctor import _check_bucket, _postgres_checks
from infrastructure.storage.migrations import run_migrations
from tests.integration.test_mono_run_lifecycle import checkpoint, claim, ready
from tests.integration.test_mono_transactions import candidate, setup_store


async def invoke(pg_database, session, owner):
    _, database = pg_database
    dsn = (
        urlsplit(Settings.load().database_url.get_secret_value())
        ._replace(path="/" + database)
        .geturl()
    )
    env = dict(os.environ, DATABASE_URL=dsn, DR4A_ENV="development")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cli",
        "dump",
        str(session),
        "--owner",
        str(owner),
        "--json",
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return process.returncode, json.loads(stdout), stderr.decode()


async def test_dump_returns_rework_seq_not_highest_phase_and_does_not_write(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    leased = await claim(store, str(uuid4()))
    for seq, phase in [(2, "research"), (3, "analyze"), (4, "research")]:
        point = checkpoint(commit, seq, phase=phase, elapsed=seq)
        async with store.transaction() as tx:
            leased = await store.research.commit_checkpoint(leased, seq - 1, point, tx)
    before = dict(await pool.fetchrow("SELECT revision,status FROM sessions"))
    code, body, stderr = await invoke(pg_database, commit.session.session_id, user.user_id)
    assert code == 0 and stderr == ""
    assert body["error"] is None and body["phase"] == "research"
    assert body["checkpoint_seq"] == 4
    assert body["state"] == point.state.model_dump(mode="json")
    assert dict(await pool.fetchrow("SELECT revision,status FROM sessions")) == before
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 4
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_dump_missing_checkpoint_and_session_are_distinct(pg_database):
    pool, store, user = await setup_store(pg_database)
    change = candidate(user.user_id)
    async with store.transaction() as tx:
        await store.research.commit_session_change(0, change, tx)
    code, body, _ = await invoke(pg_database, change.session.session_id, user.user_id)
    assert code == 1 and body["error"]["code"] == "checkpoint_not_found"
    code, body, _ = await invoke(pg_database, uuid4(), user.user_id)
    assert code == 1 and body["error"]["code"] == "session_not_found"
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0


async def test_dump_existing_other_owner_cannot_read_session(pg_database):
    pool, store, user = await setup_store(pg_database)
    commit = await ready(store, user.user_id)
    other = user.model_copy(update={"user_id": uuid4(), "email": "other@example.org"})
    async with store.transaction() as tx:
        await store.users.create(other, tx)
    code, body, _ = await invoke(pg_database, commit.session.session_id, other.user_id)
    assert code == 1 and body["error"]["code"] == "session_not_found"
    assert "state" not in body and "run_id" not in body
    code, body, _ = await invoke(pg_database, commit.session.session_id, uuid4())
    assert code == 1 and body["error"]["code"] == "owner_not_found"
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1


async def test_dump_legacy_schema_is_not_migrated_or_read_as_mono(pg_database):
    pool, _ = pg_database
    await run_migrations(pool, through_version="0001_init")
    code, body, _ = await invoke(pg_database, uuid4(), uuid4())
    assert code == 3 and body["error"]["code"] == "schema_incompatible"
    assert body["error"]["retryable"] is False
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 1
    assert await pool.fetchval("SELECT to_regclass('research_runs')") is None


async def test_doctor_schema_probe_is_readonly_and_distinguishes_legacy(pg_database):
    pool, database = pg_database
    settings = Settings.load()
    dsn = urlsplit(settings.database_url.get_secret_value())._replace(path="/" + database).geturl()
    settings = settings.model_copy(update={"database_url": type(settings.database_url)(dsn)})
    assert await _postgres_checks(settings) == (True, False)
    assert await pool.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='public'") == 0
    await run_migrations(pool, through_version="0001_init")
    assert await _postgres_checks(settings) == (True, False)
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 1
    await run_migrations(pool)
    assert await _postgres_checks(settings) == (True, True)
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
    assert await pool.fetchval("SELECT count(*) FROM users") == 0
    await pool.execute("INSERT INTO schema_migrations(version) VALUES('9999_unknown')")
    assert await _postgres_checks(settings) == (True, False)


async def test_doctor_bucket_probe_does_not_write_or_create(object_cache):
    settings = Settings.load().model_copy(update={"minio_bucket": object_cache.bucket})
    assert await _check_bucket(settings)
    assert not await _check_bucket(
        settings.model_copy(update={"minio_bucket": object_cache.bucket + "-absent"})
    )
    assert (
        await object_cache._io(lambda: list(object_cache._client.list_objects(object_cache.bucket)))
        == []
    )
