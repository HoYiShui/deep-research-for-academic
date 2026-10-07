"""Lifespan-owned scheduling through HTTP and real PG; explicit controlled work."""

import asyncio

import httpx
import pytest

from application.bootstrap import HttpRuntime
from application.errors import AppError
from application.identity import DEVELOPMENT_USER_ID
from application.phase_executor import PhaseExecutor
from application.phase_tools import ModelBinding
from application.report_serializer import ReportPublisher
from application.run_driver import RunDriver
from application.settings import Settings
from infrastructure.clock import SystemClock
from infrastructure.storage.migrations import run_migrations
from infrastructure.storage.research_postgres import PostgresResearchStore
from interface.main import create_app
from tests.integration.test_mono_clarify_http import Model, key
from tests.integration.test_mono_phase_tools import ControlledModel
from tests.integration.test_mono_run_driver import controlled_worker
from tests.integration.test_mono_task_runner import wait_for_status


async def configured(pg_database, executor_factory):
    pool, _ = pg_database
    await run_migrations(pool)
    settings = Settings(lease_s=3, heartbeat_s=1, scan_s=1, shutdown_s=1)
    model = Model()
    runtime = HttpRuntime(
        settings=settings,
        research_store=PostgresResearchStore(pool),
        llm=model,
        run_executor_factory=executor_factory,
    )
    app = create_app(settings=settings, container_factory=lambda _: runtime)
    return pool, runtime, app


async def confirm(http):
    response = await http.post("/research", json={"query": "Controlled question"}, headers=key())
    assert response.status_code == 201
    initial = response.json()
    path = f"/research/{initial['session_id']}"
    response = await http.post(
        path + "/confirm", json={"accepted": True, "brief_version": 1}, headers=key()
    )
    assert response.status_code == 202
    return path, response.json()


async def test_cli_preparation_skips_http_runner_even_when_debug_execution_is_enabled(pg_database):
    def forbidden_factory(runtime):
        pytest.fail("CLI service preparation must not compose an HTTP executor")

    _, runtime, _ = await configured(pg_database, forbidden_factory)
    runtime.settings = runtime.settings.model_copy(update={"dr4a_debug_runner": True})
    try:
        await runtime.prepare(start_runner=False)
        assert runtime.runner is None and runtime.debug_execution is None
        assert runtime.research and runtime.research_queries and runtime.run_events
    finally:
        await runtime.aclose()


async def test_http_confirmation_wakes_owned_runner_and_exception_is_persisted(pg_database):
    calls = []

    def factory(runtime):
        # All services/projections are prepared before executor construction.
        assert runtime.run_events and runtime.research_queries

        async def execute(claimed, stop):
            calls.append(claimed.run.run_id)
            raise AppError("service_not_ready", "Controlled worker not configured")

        return execute

    pool, runtime, app = await configured(pg_database, factory)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        path, accepted = await confirm(http)
        run_id = await pool.fetchval("SELECT run_id FROM research_runs")
        failed = await wait_for_status(
            runtime.repository_store, DEVELOPMENT_USER_ID, run_id, "failed"
        )
        assert failed.failure.code == "service_not_ready"
        assert not failed.resume_allowed and calls == [run_id]
        assert accepted["status"] == "ready"
        assert (await http.get(path)).json()["status"] == "failed"
        assert (await http.get(path + "/report")).status_code == 409
        await runtime.runner.tick()
        assert calls == [run_id]
        assert await pool.fetchval("SELECT count(*) FROM reports") == 0
    assert runtime.runner.closed and runtime.runner.active is None


async def test_runtime_shutdown_persists_before_closing_model(pg_database):
    entered = asyncio.Event()

    def factory(runtime):
        async def execute(claimed, stop):
            entered.set()
            await asyncio.Event().wait()

        return execute

    pool, runtime, app = await configured(pg_database, factory)
    closed = []

    async def close_model():
        closed.append(await pool.fetchval("SELECT status FROM research_runs"))

    runtime.llm.aclose = close_model
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        await confirm(http)
        await asyncio.wait_for(entered.wait(), timeout=5)
    assert closed == ["failed"]
    run_id = await pool.fetchval("SELECT run_id FROM research_runs")
    failed = await runtime.repository_store.research.get_run(DEVELOPMENT_USER_ID, run_id)
    assert failed.failure.code == "interrupted" and failed.resume_allowed
    assert failed.attempt_count == 1 and failed.checkpoint_seq == 1
    assert runtime.runner.closed and runtime.runner.active is None


async def test_invalid_executor_factory_cleans_model_without_claiming(pg_database):
    pool, runtime, app = await configured(pg_database, lambda _: None)
    closed = []

    async def close_model():
        closed.append(True)

    runtime.llm.aclose = close_model
    with pytest.raises(TypeError, match="executor"):
        async with app.router.lifespan_context(app):
            pytest.fail("Invalid composition must not accept requests")
    assert closed == [True]
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    assert not hasattr(app.state, "container")


async def test_http_confirm_driver_publish_and_read_report(pg_database, object_cache):
    visits, phases, units, terminal = [], [], [], []
    model = ControlledModel(Settings().llm_model)

    def factory(runtime):
        settings = runtime.settings
        driver = RunDriver(
            store=runtime.repository_store,
            cache=object_cache,
            executor=PhaseExecutor(
                dict.fromkeys(
                    ["plan", "research", "analyze", "write", "review"], controlled_worker(visits)
                )
            ),
            model=ModelBinding(
                model, "anthropic_compatible", settings.llm_model, settings.llm_revision, 1000
            ),
            model_slots=asyncio.Semaphore(2),
            clock=SystemClock(),
            publish=ReportPublisher(runtime.repository_store, SystemClock()).publish,
            unit_committed=units.append,
            phase_committed=phases.append,
            finished=terminal.append,
            diagnostic=lambda event: None,
        )
        return driver.execute

    pool, runtime, app = await configured(pg_database, factory)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        path, _ = await confirm(http)
        run_id = await pool.fetchval("SELECT run_id FROM research_runs")
        completed = await wait_for_status(
            runtime.repository_store, DEVELOPMENT_USER_ID, run_id, "completed"
        )
        # PG completion precedes the lossy finished projection by design.
        # Join the owned task before asserting its in-process callback, rather
        # than assuming a separate SQL observer waits for that callback too.
        active = runtime.runner.active
        if active is not None:
            await asyncio.wait_for(asyncio.shield(active), timeout=5)
        assert completed.phase == "done" and completed.checkpoint_seq == 20
        assert len(visits) == len(units) == 14 and len(phases) == 4 and len(terminal) == 1
        assert len(model.prompts) == 1
        assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
        assert await pool.fetchval("SELECT count(*) FROM reports") == 1
        assert (await http.get(path)).json()["status"] == "completed"
        report = await http.get(path + "/report")
        assert report.status_code == 200
        assert "needs_more_work" in report.text
        # A late/reconnected stream only reads committed PG facts.
        for _ in range(2):
            events = await http.get(path + "/events")
            assert events.status_code == 200 and "event: done" in events.text
            assert '"status": "completed"' in events.text
        assert len(model.prompts) == 1
        assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1
