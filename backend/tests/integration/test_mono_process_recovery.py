"""Independent SIGKILL windows, real PG/MinIO, explicitly controlled tool output."""

import asyncio
import os
import signal
import sys
from uuid import uuid4

import pytest

from application.tool_budget import ToolBudgetRequest
from application.tool_calls import ToolOutput
from domain.ports import AdapterError
from tests.integration.test_mono_run_lifecycle import claim, resume
from tests.integration.test_mono_tool_cache import identity, service, started

CHILD = """
import asyncio, os, asyncpg
from application.records import ClaimedRun
from application.settings import Settings
from application.tool_budget import ToolBudgetRequest
from application.tool_calls import ToolCallService, ToolOutput
from domain.research.tool_calls import ToolCallIdentity
from infrastructure.clock import SystemClock
from infrastructure.storage.content_cache import MinioResultCache
from infrastructure.storage.research_postgres import PostgresResearchStore

async def main():
    settings = Settings.load()
    pool = await asyncpg.create_pool(settings.database_url.get_secret_value(),
        database=os.environ['DR4A_RECOVERY_DATABASE'], min_size=1, max_size=2)
    store = PostgresResearchStore(pool)
    claimed = ClaimedRun.model_validate_json(os.environ['DR4A_RECOVERY_CLAIM'])
    identity = ToolCallIdentity.model_validate_json(os.environ['DR4A_RECOVERY_IDENTITY'])
    cache = MinioResultCache(settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        os.environ['DR4A_RECOVERY_BUCKET'], secure=settings.minio_secure)

    class CrashWindow:
        async def put(self, namespace, body, media_type):
            if os.environ['DR4A_RECOVERY_WRITTEN'] == '1':
                await cache.put(namespace, body, media_type)
            print('READY_FOR_SIGKILL', flush=True)
            await asyncio.Event().wait()

        async def read(self, reference):
            return await cache.read(reference)

    async def operation():
        return ToolOutput(content={'controlled': 'not a real provider call'}, tokens_used=73)

    await ToolCallService(claimed, store, store.research, CrashWindow(),
        SystemClock(), elapsed_base=0).invoke(identity,
        ToolBudgetRequest(tool='llm', token_reservation=100, terminal=False), operation)

asyncio.run(main())
"""


@pytest.mark.parametrize("written", [False, True])
async def test_sigkill_staged_result_recovers_without_reset_or_implicit_replay(
    pg_database, object_cache, written
):
    pool, store, user, commit, claimed = await started(pg_database)
    value = identity(claimed, tool="llm")
    env = os.environ | {
        "DR4A_RECOVERY_DATABASE": pg_database[1],
        "DR4A_RECOVERY_BUCKET": object_cache.bucket,
        "DR4A_RECOVERY_CLAIM": claimed.model_dump_json(),
        "DR4A_RECOVERY_IDENTITY": value.model_dump_json(),
        "DR4A_RECOVERY_WRITTEN": "1" if written else "0",
    }
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CHILD,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        marker = await asyncio.wait_for(child.stdout.readline(), timeout=20)
        assert marker == b"READY_FOR_SIGKILL\n", "Child did not reach the controlled crash window"
        assert await pool.fetchval("SELECT status FROM tool_calls") == "reserved"
        assert await pool.fetchval("SELECT staged_tokens FROM tool_call_attempts") == 73
        child.kill()  # SIGKILL: no Python finally, cancellation or failure cleanup.
        await asyncio.wait_for(child.wait(), timeout=5)
        assert child.returncode == -signal.SIGKILL
    finally:
        if child.returncode is None:
            child.kill()
        await asyncio.wait_for(child.communicate(), timeout=5)

    assert await pool.fetchval("SELECT status FROM tool_calls") == "reserved"
    assert await pool.fetchval("SELECT tokens_used FROM tool_call_attempts") is None
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        await store.research.scan_interrupted(tx)
    assert await claim(store, str(uuid4())) is None  # Never auto-restart paid work.
    await resume(store, user.user_id, commit, 1)
    newer = await claim(store, str(uuid4()))
    assert newer.run.run_id == claimed.run.run_id and newer.run.lease_token == 2
    assert newer.run.checkpoint_seq == 1 and newer.run.attempt_count == 2
    budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
    assert budget.used.tokens == 73 and budget.used.llm_calls == 1
    assert budget.pending.tokens == 0
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return ToolOutput(content={"controlled": "not a real provider call"}, tokens_used=73)

    runner = service(store, newer, object_cache)
    request = ToolBudgetRequest(tool="llm", token_reservation=100, terminal=False)
    if not written:
        with pytest.raises(AdapterError, match="content_missing"):
            await runner.invoke(value, request, operation)
        assert calls == 0
        await runner.invoke(value, request, operation, allow_uncertain_replay=True)
    else:
        await runner.invoke(value, request, operation)
    assert calls == (0 if written else 1)
    rows = await pool.fetch(
        "SELECT attempt,lease_token,status,tokens_used,uncertain_replay "
        "FROM tool_call_attempts ORDER BY attempt"
    )
    assert len(rows) == (1 if written else 2)
    assert rows[0]["lease_token"] == 1 and rows[0]["tokens_used"] == 73
    assert rows[0]["status"] == ("succeeded" if written else "uncertain")
    if not written:
        assert rows[1]["uncertain_replay"] and rows[1]["lease_token"] == 2
    budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
    assert budget.used.tokens == (73 if written else 146)
    assert budget.used.llm_calls == (1 if written else 2)
    assert await pool.fetchval("SELECT status FROM tool_calls") == "succeeded"
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
