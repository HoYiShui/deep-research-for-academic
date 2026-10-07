"""Actual TypeScript client -> live FastAPI -> isolated PG; controlled model."""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from application.settings import Settings
from cli.phase_state import load_phase_state
from domain.research.state import PipelineState
from tests.integration.test_verify_clarify_http import server


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
