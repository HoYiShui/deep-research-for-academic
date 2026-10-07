"""CLI real path: actual PG/MinIO/SDK transport, controlled model only."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest

from application.settings import Settings
from cli.phase_tools import DebugTools
from cli.run_real import cancel_owned
from infrastructure.storage.migrations import run_migrations
from tests.integration.test_mono_tool_cache import started
from tests.support.model_server import model_server
from tests.unit.test_state import initial_state


async def command(path, database, bucket, url, *extra, session=None):
    settings = Settings.load()
    parts = urlsplit(settings.database_url.get_secret_value())
    arguments = (
        ["run", "--brief", str(path), "--real", "--json"]
        if session is None
        else ["dump", session, "--json"]
    )
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cli",
        *arguments,
        *extra,
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ
        | {
            "DATABASE_URL": urlunsplit(parts._replace(path="/" + database)),
            "DR4A_ENV": "development",
            "DR4A_AUTH_REQUIRED": "false",
            "LLM_LOCAL": "false",
            "ANTHROPIC_API_KEY": "controlled-test-key",
            "ANTHROPIC_BASE_URL": url,
            "MINIO_BUCKET": bucket,
            "NO_PROXY": "127.0.0.1,localhost",
            "SHUTDOWN_S": "1",
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def collect(process):
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return process.returncode, json.loads(stdout), stderr.decode()


@pytest.mark.parametrize("repair", [False, True])
async def test_real_run_freezes_without_clarify_commits_plan_and_fails_missing_worker(
    pg_database, object_cache, tmp_path, repair
):
    pool, database = pg_database
    await run_migrations(pool)
    state = initial_state()
    path = tmp_path / "brief.json"
    path.write_text(state.research_brief.model_dump_json())
    response = DebugTools(state, fake=True).fake_plan()
    expected_calls = 2 if repair else 1
    async with model_server(
        Settings.load().llm_model, ["not JSON", response] if repair else response
    ) as (url, calls, _):
        process = await command(path, database, object_cache.bucket, url)
        code, body, stderr = await collect(process)
    assert code == 3 and body["error"]["code"] == "service_not_ready"
    assert body["dependency_mode"] == "real" and body["final_report"] is None
    assert body["checkpoint_seq"] == 3 and body["phase"] == "research"
    assert len(calls) == expected_calls and "FROZEN" in calls[0]["messages"][0]["content"]
    run_id = UUID(body["run_id"])
    assert (
        await pool.fetchval("SELECT status FROM research_runs WHERE run_id=$1", run_id) == "failed"
    )
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == expected_calls
    assert (
        await pool.fetchval("SELECT sum(tokens_used) FROM tool_call_attempts")
        == 50 * expected_calls
    )
    state = json.loads(
        await pool.fetchval("SELECT state FROM phase_snapshots WHERE run_id=$1 AND seq=3", run_id)
    )
    assert (
        len(state["section_plans"]) == 5
        and state["run_metadata"]["budget_used"]["tokens"] == 50 * expected_calls
    )
    assert "controlled-test-key" not in json.dumps(body) + stderr
    process = await command(path, database, object_cache.bucket, url, session=body["session_id"])
    dump_code, snapshot, _ = await collect(process)
    assert dump_code == 0 and snapshot["state"] == state
    assert snapshot["run_id"] == body["run_id"] and snapshot["checkpoint_seq"] == 3


@pytest.mark.parametrize("signal_name", [signal.SIGINT, signal.SIGTERM])
async def test_signal_real_run_cancels_only_held_run(
    pg_database, object_cache, tmp_path, signal_name
):
    pool, database = pg_database
    await run_migrations(pool)
    state = initial_state()
    path = tmp_path / "brief.json"
    path.write_text(state.research_brief.model_dump_json())
    async with model_server(Settings.load().llm_model, "unused", hold=True) as (
        url,
        calls,
        entered,
    ):
        process = await command(path, database, object_cache.bucket, url, "--quiet")
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            process.send_signal(signal_name)
            code, body, _ = await collect(process)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    assert code == 1 and body["error"]["code"] == "cancelled"
    assert "events" not in body and body["final_report"] is None
    assert (
        len(calls) == 1 and await pool.fetchval("SELECT status FROM research_runs") == "cancelled"
    )
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_signal_cannot_cancel_other_or_expired_worker(pg_database):
    _, store, user, commit, claimed = await started(pg_database)

    class Worker:
        worker_id = "not-the-owner"

    assert not await cancel_owned(Worker(), store, user.user_id, commit.run.run_id)
    assert (await store.research.get_run(user.user_id, commit.run.run_id)).status == "running"
    Worker.worker_id = claimed.run.lease_owner
    await pg_database[0].execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1",
        commit.run.run_id,
    )
    assert not await cancel_owned(Worker(), store, user.user_id, commit.run.run_id)
    assert (await store.research.get_run(user.user_id, commit.run.run_id)).status == "running"


async def test_real_cli_never_maintains_another_runs_expired_lease(
    pg_database, object_cache, tmp_path
):
    pool, store, user, foreign, claimed = await started(pg_database)
    database = pg_database[1]
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1",
        foreign.run.run_id,
    )
    before = await store.research.get_run(user.user_id, foreign.run.run_id)
    path = tmp_path / "brief.json"
    path.write_text(initial_state().research_brief.model_dump_json())
    async with model_server(Settings.load().llm_model, "unused", hold=True) as (url, _, entered):
        process = await command(path, database, object_cache.bucket, url)
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            assert await store.research.get_run(user.user_id, foreign.run.run_id) == before
            assert before.status == "running" and before.lease_token == claimed.run.lease_token
        finally:
            if process.returncode is None:
                process.send_signal(signal.SIGINT)
            await collect(process)
    assert await store.research.get_run(user.user_id, foreign.run.run_id) == before


async def test_real_run_does_not_migrate_legacy_database(pg_database, object_cache, tmp_path):
    pool, database = pg_database
    path = tmp_path / "brief.json"
    path.write_text(initial_state().research_brief.model_dump_json())
    async with model_server(Settings.load().llm_model, "unused") as (url, calls, _):
        process = await command(path, database, object_cache.bucket, url)
        code, body, _ = await collect(process)
    assert code == 3 and body["error"]["code"] == "schema_incompatible" and calls == []
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        )
        == 0
    )
