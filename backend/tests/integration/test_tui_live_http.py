"""Actual TypeScript client -> live FastAPI -> isolated PG; controlled model."""

import asyncio
import json
import os
import signal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from application.settings import Settings
from cli.phase_state import load_phase_state
from domain.research.state import PipelineState
from tests.integration.test_verify_clarify_http import server

CLIENT_IMPORTS = """
import assert from 'node:assert/strict';
import {ResearchApiClient} from './src/api-client.ts';
import {ResearchSession} from './src/session.ts';
const api=new ResearchApiClient(process.env.TEST_API_URL);
const current=new ResearchSession(api);
"""

START_RUNNING = """
await current.send('Design a public evaluation');
await current.send('Public intrusion detector evaluation');
assert.equal(current.view.status,'confirm');
await current.confirm();
const deadline=Date.now()+10000;
while(current.view.phase!=='research' && Date.now()<deadline) {
 await new Promise(resolve=>setTimeout(resolve,25)); await current.refresh();
}
assert.equal(current.view.status,'running');
assert.equal(current.view.phase,'research');
"""


async def tui_probe(url, code, **values):
    process = await asyncio.create_subprocess_exec(
        "node",
        "--import",
        "tsx",
        "--input-type=module",
        "-e",
        CLIENT_IMPORTS + code,
        cwd=Path(__file__).resolve().parents[3] / "tui",
        env=os.environ | {"TEST_API_URL": url} | values,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=25)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stderr.decode()
    return json.loads(stdout)


async def test_tui_cancels_live_owned_execution_and_observes_durable_terminal(
    pg_database,
    object_cache,
):
    pool, database = pg_database
    async with server(database, run_bucket=object_cache.bucket, pause="research") as url:
        result = await tui_probe(
            url,
            START_RUNNING
            + """
const events=[];
let subscribed;
const opened=new Promise(resolve=>{subscribed=resolve});
const observation=current.observe(event=>{
 events.push(event);
 if(event.event==='phase') subscribed();
},()=>{},error=>{throw error},25);
await opened;
await current.cancel();
assert.equal(current.view.status,'cancelling');
await observation;
assert.equal(current.view.status,'cancelled');
assert.equal(current.view.resume_allowed,false);
assert(events.some(event=>event.event==='done' && event.data.status==='cancelled'));
assert(!events.some(event=>event.event==='done' && event.data.status==='completed'));
await assert.rejects(()=>api.report(current.view.session_id),error=>error.code==='report_not_ready');
await current.cancel(); assert.equal(current.view.status,'cancelled');
console.log(JSON.stringify(current.view));
""",
        )
    assert result["status"] == "cancelled"
    assert await pool.fetchval("SELECT status FROM sessions") == "cancelled"
    assert await pool.fetchval("SELECT status FROM research_runs") == "cancelled"
    assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_tui_reopens_crashed_run_then_explicitly_resumes_same_checkpoint(
    pg_database,
    object_cache,
):
    pool, database = pg_database
    async with server(
        database, run_bucket=object_cache.bucket, pause="research", with_process=True
    ) as (url, process):
        original = await tui_probe(
            url, START_RUNNING + "console.log(JSON.stringify(current.view));"
        )
        process.send_signal(signal.SIGKILL)
        await asyncio.wait_for(process.wait(), timeout=5)
    # Advance only the invocation-owned fixture's dead lease instead of waiting
    # the production 90s. No persisted state or checkpoints are fabricated.
    await pool.execute(
        "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE run_id=$1::uuid",
        original["run_id"],
    )
    async with server(database) as url:
        resumed = await tui_probe(
            url,
            """
await current.open(process.env.TEST_SESSION_ID);
const deadline=Date.now()+10000;
while(current.view.status!=='failed' && Date.now()<deadline) {
 await new Promise(resolve=>setTimeout(resolve,25)); await current.refresh();
}
assert.equal(current.view.status,'failed');
assert.equal(current.view.failure.code,'interrupted');
assert.equal(current.view.resume_allowed,true);
assert.equal(current.view.checkpoint_seq,Number(process.env.TEST_SEQ));
await current.resume();
await current.refresh(); assert.equal(current.view.status,'ready');
assert.equal(current.view.run_id,process.env.TEST_RUN_ID);
console.log(JSON.stringify(current.view));
""",
            TEST_SESSION_ID=original["session_id"],
            TEST_RUN_ID=original["run_id"],
            TEST_SEQ=str(original["checkpoint_seq"]),
        )
        assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1
        assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert resumed["checkpoint_seq"] == original["checkpoint_seq"] == 3
    async with server(database, run_bucket=object_cache.bucket, pause="progress") as url:
        completed = await tui_probe(
            url,
            """
await current.open(process.env.TEST_SESSION_ID);
const events=[];
await current.observe(event=>events.push(event),()=>{},error=>{throw error},25);
assert.equal(current.view.status,'completed');
assert(events.some(event=>event.event==='progress' && event.data.stage==='query_completed'));
assert(events.some(event=>event.event==='done' && event.data.status==='completed'));
const report=await api.report(current.view.session_id);
assert.equal(report.review_verdict,'needs_more_work');
assert.equal(typeof report.report,'string');
console.log(JSON.stringify(current.view));
""",
            TEST_SESSION_ID=original["session_id"],
        )
    assert completed["run_id"] == original["run_id"]
    assert completed["checkpoint_seq"] == 20
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 2
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
    assert await pool.fetchval("SELECT count(*) FROM reports") == 1


async def test_tui_clarify_feedback_confirm_sse_and_cli_share_persisted_session(pg_database):
    pool, database = pg_database
    tui = Path(__file__).resolve().parents[3] / "tui"
    code = """
import assert from 'node:assert/strict';
import {ResearchApiClient} from './src/api-client.ts';
import {ResearchSession} from './src/session.ts';
const api = new ResearchApiClient(process.env.TEST_API_URL);
const current = new ResearchSession(api);
await current.send('Design a public evaluation'); assert.equal(current.view.status,'ask');
await current.send('Public intrusion detector evaluation'); assert.equal(current.view.status,'confirm');
await current.send('Clarify scope and retain explicit assumptions'); assert.equal(current.view.status,'ask');
await current.send('Public data and evaluation protocol'); assert.equal(current.view.status,'confirm');
const id=current.view.session_id, version=current.view.brief_version;
await current.confirm(); assert.equal(current.view.status,'ready');
assert.equal(current.view.brief_version,version);
const restored=new ResearchSession(api); await restored.open(id); assert.equal(restored.view.status,'ready');
for await (const event of api.events(restored.view.sse_url)) {
 assert.equal(event.event,'phase'); assert.equal(event.data.session_id,id); break;
}
console.log(JSON.stringify({session_id:id,brief_version:version,status:restored.view.status}));
"""
    async with server(database) as url:
        process = await asyncio.create_subprocess_exec(
            "node",
            "--import",
            "tsx",
            "--input-type=module",
            "-e",
            code,
            cwd=tui,
            env=os.environ | {"TEST_API_URL": url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, stderr.decode()
        result = json.loads(stdout)
        settings = Settings.load()
        dsn = urlunsplit(
            urlsplit(settings.database_url.get_secret_value())._replace(path="/" + database)
        )
        dump = await asyncio.create_subprocess_exec(
            str(Path(__file__).resolve().parents[2] / ".venv/bin/python"),
            "-m",
            "cli",
            "dump",
            result["session_id"],
            "--json",
            cwd=tui.parent / "backend",
            env=os.environ | {"DATABASE_URL": dsn, "DR4A_ENV": "development"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            body, stderr = await asyncio.wait_for(dump.communicate(), timeout=15)
        finally:
            if dump.returncode is None:
                dump.kill()
                await dump.wait()
        assert dump.returncode == 0, stderr.decode()
        state = PipelineState.model_validate(json.loads(body)["state"])
        assert load_phase_state(json.loads(body), "plan") == state
        assert str(state.session_id) == result["session_id"]
        assert state.phase == "plan" and state.brief_version == result["brief_version"]
        assert await pool.fetchval("SELECT count(*) FROM sessions") == 1
        assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
        assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
        cancel_code = """
import assert from 'node:assert/strict';
import {ResearchApiClient} from './src/api-client.ts';
import {ResearchSession} from './src/session.ts';
const session=new ResearchSession(new ResearchApiClient(process.env.TEST_API_URL));
await session.open(process.env.TEST_SESSION_ID);
assert.equal(session.view.status,'ready');
await session.cancel();
assert.equal(session.view.status,'cancelling');
const deadline=Date.now()+5000;
while(session.view.status==='cancelling' && Date.now()<deadline) {
 await new Promise(resolve=>setTimeout(resolve,25)); await session.refresh();
}
assert.equal(session.view.status,'cancelled');
console.log(JSON.stringify({status:session.view.status}));
"""
        child = await asyncio.create_subprocess_exec(
            "node",
            "--import",
            "tsx",
            "--input-type=module",
            "-e",
            cancel_code,
            cwd=tui,
            env=os.environ | {"TEST_API_URL": url, "TEST_SESSION_ID": result["session_id"]},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            body, errors = await asyncio.wait_for(child.communicate(), timeout=10)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
        assert child.returncode == 0, errors.decode()
        assert json.loads(body)["status"] == "cancelled"
        assert await pool.fetchval("SELECT status FROM sessions") == "cancelled"
        assert await pool.fetchval("SELECT status FROM research_runs") == "cancelled"
        assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 0
        assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 0
        assert await pool.fetchval("SELECT count(*) FROM reports") == 0
