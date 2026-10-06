"""Live TCP and independent server process, real PG/MinIO, controlled workers."""

import asyncio
import json
import sys

import httpx

from scripts.verify_clarify_http import verify
from scripts.verify_run_http import verify as verify_run
from tests.integration.test_verify_clarify_http import server


async def test_confirm_live_driver_report_and_reconnect_after_process_restart(
    pg_database, object_cache
):
    pool, database = pg_database
    async with (
        server(database, run_bucket=object_cache.bucket) as url,
        httpx.AsyncClient(base_url=url, timeout=15) as http,
    ):
        result = await verify(http, query="Design a public controlled evaluation")
        session = result["view"]["session_id"]
        result = await verify(http, session_id=session, answers=[{"content": "Public evaluation"}])
        view = result["view"]
        assert view["status"] == "confirm"
        approval = {
            "session_id": session,
            "brief_version": view["brief_version"],
            "research_brief": view["research_brief"],
        }
        await verify(http, session_id=session, approval=approval)
        path = f"/research/{session}"
        # Real streamed HTTP, not ASGI's buffered in-process response.
        async with asyncio.timeout(15), http.stream("GET", path + "/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            event, done = None, None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ")
                if line.startswith("data: ") and event == "done":
                    done = json.loads(line.removeprefix("data: "))
                    break
            assert done is not None and done["status"] == "completed"
            assert done["checkpoint_seq"] == 20
            assert done["review_verdict"] == "needs_more_work"
        report = (await http.get(path + "/report")).json()
        assert await pool.fetchval("SELECT count(*) FROM reports") == 1
        assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
    # Discard all process tasks, model instances and SSE queues. PG facts remain.
    async with (
        server(database, run_bucket=object_cache.bucket) as url,
        httpx.AsyncClient(base_url=url, timeout=15) as http,
    ):
        assert (await http.get(path)).json()["status"] == "completed"
        assert (await http.get(path + "/report")).json() == report
        events = await http.get(path + "/events")
        assert events.status_code == 200 and "event: done" in events.text
        checked = await verify_run(http, session_id=session)
        assert checked["status"] == "completed" and checked["report"] == report
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "scripts.verify_run_http",
            "--url",
            url,
            "--session",
            session,
            "--model-mode",
            "controlled",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, stderr.decode()
        probe = json.loads(stdout)
        assert probe["status"] == "completed" and probe["model_mode"] == "controlled"
        assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1
        assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
