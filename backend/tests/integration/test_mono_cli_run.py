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
from infrastructure.parser.html import HTML_PARSER_VERSION
from infrastructure.storage.migrations import run_migrations
from tests.integration.test_mono_tool_cache import started
from tests.support.model_server import model_server
from tests.unit.test_state import initial_state


async def command(
    path,
    database,
    bucket,
    url,
    *extra,
    session=None,
    parser_version=HTML_PARSER_VERSION,
    partial_executor=True,
    fetch_exhaustion=False,
):
    settings = Settings.load()
    parts = urlsplit(settings.database_url.get_secret_value())
    arguments = (
        ["run", "--brief", str(path), "--real", "--json"]
        if session is None
        else ["dump", session, "--json"]
    )
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        f"""
import sys
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.openalex import OpenAlexSearch
from infrastructure.search.search_router import SearchRouterSearch
import application.debug_runtime as runtime_module
from application.phase_workers import plan_worker, research_worker
# This legacy failure-path test deliberately binds an incomplete executor.
# New complete-runtime tests exercise the actual default worker registration.
if {partial_executor!r}:
    runtime_module.public_workers = lambda: {{"plan": plan_worker, "research": research_worker}}
from cli.__main__ import main
async def empty_search(self, query):
    if {fetch_exhaustion!r}:
        from domain.ports import SearchResult
        return [SearchResult(source_id=f"blocked-{{n}}", source_type="web", title="Controlled blocked candidate",
            snippet="Not evidence", url=f"http://127.0.0.1/{{n}}", provider="bocha") for n in (1, 2)]
    return []
# Explicit child-only controlled providers, never a production fallback.
ArxivSearch.search = BochaSearch.search = OpenAlexSearch.search = empty_search
SearchRouterSearch.search = empty_search
sys.exit(main())
""",
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
            "PARSER_VERSION": parser_version,
            "RUN_FETCH_CALLS": "1" if fetch_exhaustion else str(settings.run_fetch_calls),
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


async def test_default_five_workers_publish_real_pg_report_with_controlled_external_io(
    pg_database, object_cache, tmp_path
):
    """No worker replacements: SDK transport, all workers, ledger and publisher."""
    from domain.research.state import PipelineState

    pool, database = pg_database
    await run_migrations(pool)
    initial = initial_state()
    fake = DebugTools(initial, fake=True)
    path = tmp_path / "brief.json"
    path.write_text(initial.research_brief.model_dump_json())

    def response(request):
        prompt = request["messages"][0]["content"]
        if "<chapter_context>" in prompt:
            return fake.fake_draft(prompt)
        if "<review_context>" in prompt:
            return json.dumps({"issues": [], "prior_issues": [], "verdict": "needs_more_work"})
        return fake.fake_plan()

    async with model_server(Settings.load().llm_model, response) as (url, calls, _):
        process = await command(
            path, database, object_cache.bucket, url, "--verbose", partial_executor=False
        )
        code, body, stderr = await collect(process)
    assert code == 0, (body.get("error"), stderr)
    assert len(calls) == 7  # plan + five actual writer calls + actual critic
    assert body["phase"] == "done" and body["final_report"]
    run_id = UUID(body["run_id"])
    assert (
        await pool.fetchval("SELECT status FROM research_runs WHERE run_id=$1", run_id)
        == "completed"
    )
    assert await pool.fetchval("SELECT count(*) FROM reports") == 1
    snapshot = json.loads(
        await pool.fetchval(
            "SELECT state FROM phase_snapshots WHERE run_id=$1 ORDER BY seq DESC LIMIT 1", run_id
        )
    )
    state = PipelineState.model_validate(snapshot)
    assert state.review_verdict == "needs_more_work" and state.draft_version == 1
    assert len(state.draft_sections) == 5 and not state.evidence
    assert all(claim.claim_type == "hypothesis" for claim in state.claims.values())
    assert any(item.operation == "analysis_skipped" for item in state.run_metadata.degraded_sources)
    assert state.final_report.risks and not state.final_report.references
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 17
    assert "controlled-test-key" not in json.dumps(body) + stderr
    await fake.close()


async def test_mid_query_fetch_budget_contracts_once_without_fake_query_completion(
    pg_database, object_cache, tmp_path
):
    from domain.research.state import PipelineState

    pool, database = pg_database
    await run_migrations(pool)
    initial = initial_state()
    fake = DebugTools(initial, fake=True)
    path = tmp_path / "brief.json"
    path.write_text(initial.research_brief.model_dump_json())

    def response(request):
        prompt = request["messages"][0]["content"]
        if "<chapter_context>" in prompt:
            return fake.fake_draft(prompt)
        if "<review_context>" in prompt:
            return json.dumps({"issues": [], "prior_issues": [], "verdict": "approved"})
        return fake.fake_plan()

    async with model_server(Settings.load().llm_model, response) as (url, calls, _):
        process = await command(
            path,
            database,
            object_cache.bucket,
            url,
            "--verbose",
            partial_executor=False,
            fetch_exhaustion=True,
        )
        code, body, stderr = await collect(process)
    assert code == 0, (body.get("error"), stderr)
    assert len(calls) == 7
    state = PipelineState.model_validate_json(
        await pool.fetchval("SELECT state::text FROM phase_snapshots ORDER BY seq DESC LIMIT 1")
    )
    assert state.phase == "done" and state.review_verdict == "needs_more_work"
    assert state.run_metadata.stop_reason == "budget_exhausted"
    assert state.run_metadata.budget_used.fetch_calls == 1
    assert state.run_metadata.budget_used.search_calls == 2  # web + papers
    assert not state.sources and not state.evidence and not state.analysis_artifacts
    assert [entry.phase for entry in state.run_metadata.unit_manifest.values()].count(
        "research"
    ) == 0
    assert all(coverage.gaps for coverage in state.section_coverage.values())
    assert stderr.count("stage=query_started") == 1 and "stage=query_completed" not in stderr
    assert await pool.fetchval("SELECT count(*) FROM reports") == 1
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts WHERE tool='fetch'") == 1
    await fake.close()


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
        process = await command(path, database, object_cache.bucket, url, "--verbose")
        code, body, stderr = await collect(process)
    assert code == 3 and body["error"]["code"] == "service_not_ready"
    assert "run_accepted session_id=" in stderr
    assert stderr.count("stage=query_started") == stderr.count("stage=query_completed") == 5
    assert stderr.count("stage=section_completed") == 5
    assert "persistence=uncommitted" in stderr and "persistence=pg_checkpoint" in stderr
    assert body["dependency_mode"] == "real" and body["final_report"] is None
    assert body["checkpoint_seq"] == 14 and body["phase"] == "analyze"
    assert len(calls) == expected_calls and "FROZEN" in calls[0]["messages"][0]["content"]
    run_id = UUID(body["run_id"])
    assert (
        await pool.fetchval("SELECT status FROM research_runs WHERE run_id=$1", run_id) == "failed"
    )
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == expected_calls + 10
    assert (
        await pool.fetchval("SELECT sum(tokens_used) FROM tool_call_attempts")
        == 50 * expected_calls
    )
    state = json.loads(
        await pool.fetchval("SELECT state FROM phase_snapshots WHERE run_id=$1 AND seq=14", run_id)
    )
    assert (
        len(state["section_plans"]) == 5
        and state["run_metadata"]["budget_used"]["tokens"] == 50 * expected_calls
    )
    assert "controlled-test-key" not in json.dumps(body) + stderr
    assert len(state["section_coverage"]) == 5 and not state["evidence"]
    assert all(coverage["gaps"] for coverage in state["section_coverage"].values())
    assert state["run_metadata"]["budget_used"]["search_calls"] == 10
    assert any(event["event"] == "progress" for event in body["events"])
    process = await command(path, database, object_cache.bucket, url, session=body["session_id"])
    dump_code, snapshot, _ = await collect(process)
    assert dump_code == 0 and snapshot["state"] == state
    assert snapshot["run_id"] == body["run_id"] and snapshot["checkpoint_seq"] == 14


async def test_verbose_identity_is_available_while_model_waits_and_can_be_dumped(
    pg_database, object_cache, tmp_path
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
        process = await command(path, database, object_cache.bucket, url, "--verbose", "--quiet")
        try:
            line = (await asyncio.wait_for(process.stderr.readline(), timeout=10)).decode()
            assert "run_accepted session_id=" in line and "status=ready" in line
            session = line.split("session_id=", 1)[1].split()[0]
            run_id = line.split("run_id=", 1)[1].split()[0]
            await asyncio.wait_for(entered.wait(), timeout=10)
            assert process.returncode is None and len(calls) == 1
            dump = await command(path, database, object_cache.bucket, url, session=session)
            dump_code, snapshot, _ = await collect(dump)
            assert dump_code == 0 and snapshot["run_id"] == run_id
            assert snapshot["checkpoint_seq"] == 1 and snapshot["state"]["phase"] == "plan"
            process.send_signal(signal.SIGINT)
            code, body, stderr = await collect(process)
            assert code == 1 and body["session_id"] == session and body["run_id"] == run_id
            assert "events" not in body and "stage=query_completed" not in stderr
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


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


async def test_unconfigured_research_parser_fails_before_frozen_run_or_model_call(
    pg_database, object_cache, tmp_path
):
    pool, database = pg_database
    await run_migrations(pool)
    path = tmp_path / "brief.json"
    path.write_text(initial_state().research_brief.model_dump_json())
    async with model_server(Settings.load().llm_model, "unused") as (url, calls, _):
        process = await command(
            path, database, object_cache.bucket, url, parser_version="unconfigured"
        )
        code, body, _ = await collect(process)
    assert code == 3 and body["error"]["code"] == "service_not_ready"
    assert calls == []
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
