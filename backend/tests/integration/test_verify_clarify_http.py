"""Black-box verifier against a live TCP server and isolated PostgreSQL."""

import asyncio
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
import pytest

from application.settings import Settings
from scripts.verify_clarify_http import VerificationError, verify


@asynccontextmanager
async def server(database, *, real=False):
    settings = Settings.load()
    parts = urlsplit(settings.database_url.get_secret_value())
    dsn = urlunsplit(parts._replace(path="/" + database))
    with socket.socket() as address:
        address.bind(("127.0.0.1", 0))
        port = address.getsockname()[1]
    env = os.environ | {
        "DATABASE_URL": dsn,
        "DR4A_ENV": "development",
        "DR4A_AUTH_REQUIRED": "false",
        "DR4A_TEST_HTTP_MODE": "controlled",
    }
    target = "interface.main:app" if real else "tests.support.mono_http_server:create_test_app"
    args = [sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port)]
    if not real:
        args.append("--factory")
    process = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        cwd=Path(__file__).resolve().parents[2],
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(timeout=1) as client:
            for _ in range(100):
                if process.returncode is not None:
                    raise RuntimeError(f"Test HTTP server exited ({process.returncode})")
                try:
                    if (await client.get(url + "/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Test HTTP server did not start")
        yield url
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()


async def test_live_verifier_requires_exact_approval_and_recovers_after_restart(pg_database):
    pool, database = pg_database
    async with server(database) as url, httpx.AsyncClient(base_url=url, timeout=10) as http:
        result = await verify(http, query="Design an evaluation")
        assert result["view"]["status"] == "ask"
        session = result["view"]["session_id"]
        assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    # New independent process and composition root: no in-memory history survives.
    async with server(database) as url, httpx.AsyncClient(base_url=url, timeout=10) as http:
        recovered = await verify(http, session_id=session)
        assert recovered["view"]["status"] == "ask"
        result = await verify(http, session_id=session, answers=[{"content": "Public evaluation"}])
        view = result["view"]
        assert view["status"] == "confirm" and view["clarification_round"] == 1
        assert result["approval_required"]
        approval = {
            "session_id": session,
            "brief_version": view["brief_version"],
            "research_brief": view["research_brief"],
        }
        with pytest.raises(VerificationError, match="approval"):
            await verify(http, session_id=session, approval=approval | {"brief_version": 1})
        with pytest.raises(VerificationError, match="approval"):
            await verify(
                http,
                session_id=session,
                approval=approval
                | {"research_brief": view["research_brief"] | {"assumptions": "not approved"}},
            )
        assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    async with server(database) as url, httpx.AsyncClient(base_url=url, timeout=10) as http:
        assert (await verify(http, session_id=session))["view"]["status"] == "confirm"
        result = await verify(http, session_id=session, approval=approval)
        assert result["view"]["status"] == "ready"
        run = result["view"]["run_id"]
        assert result["view"]["checkpoint_seq"] == 1
        assert result["trace"][-2]["status_code"] == 202
    async with server(database) as url, httpx.AsyncClient(base_url=url, timeout=10) as http:
        view = (await verify(http, session_id=session))["view"]
        assert view["status"] == "ready" and view["run_id"] == run
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 1
    assert await pool.fetchval("SELECT count(*) FROM briefs WHERE frozen_at IS NOT NULL") == 1
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
    snapshot = json.loads(await pool.fetchval("SELECT state FROM phase_snapshots"))
    assert snapshot["run_id"] == run and snapshot["session_id"] == session
    UUID(run)


async def test_verifier_cli_is_live_http_and_never_auto_confirms(pg_database):
    pool, database = pg_database
    async with server(database) as url:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "scripts.verify_clarify_http",
            "--url",
            url,
            "--query",
            "Design evaluation",
            "--model-mode",
            "controlled",
            cwd=Path(__file__).resolve().parents[2],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        result = json.loads(stdout)
        assert result["model_mode"] == "controlled" and result["view"]["status"] == "ask"
        assert result["trace"][0]["status_code"] == 201
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0


async def test_verifier_rejects_unknown_answer_fields_before_http(pg_database):
    _pool, database = pg_database
    async with server(database) as url, httpx.AsyncClient(base_url=url, timeout=10) as http:
        with pytest.raises(VerificationError, match="answer"):
            await verify(http, query="evaluation", answers=[{"content": "ok", "accepted": True}])
