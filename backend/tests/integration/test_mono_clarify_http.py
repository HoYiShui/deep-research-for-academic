"""Mono clarification over actual HTTP routes and isolated real PostgreSQL."""

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest

from application.bootstrap import HttpRuntime
from application.errors import AppError
from application.identity import DEVELOPMENT_USER_ID
from application.records import User
from application.settings import Settings
from domain.ports import AdapterError
from domain.research.machine import assess_brief
from domain.research.models import (
    ClarifyAssessment,
    PartialResearchBrief,
    ResearchBrief,
    SourceSelection,
)
from infrastructure.clock import SystemClock
from infrastructure.storage import research_postgres
from infrastructure.storage.migrations import run_migrations
from infrastructure.storage.research_postgres import PostgresResearchStore
from interface.main import create_app
from tests.integration.test_mono_transactions import candidate
from tests.unit.test_mono_clarify import assessment, core


class Model:
    def __init__(self, complete=True):
        self.complete_brief = complete
        self.calls = 0
        self.fail = False
        self.entered = asyncio.Event()
        self.third_entered = asyncio.Event()
        self.release = None

    async def complete(self, prompt):
        self.calls += 1
        self.entered.set()
        if self.calls >= 3:
            self.third_entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise OSError("secret-provider-address")
        return json.dumps(assessment(brief_patch=core() if self.complete_brief else {}))


def key(value=None):
    return {"Idempotency-Key": value or str(uuid4())}


@pytest.fixture
async def configured(pg_database, tmp_path):
    pool, _ = pg_database
    await run_migrations(pool)
    model = Model()
    settings = Settings.load(env_file=tmp_path / "missing", environ={})
    runtime = HttpRuntime(settings=settings, research_store=PostgresResearchStore(pool), llm=model)
    app = create_app(settings=settings, container_factory=lambda config: runtime)
    async with app.router.lifespan_context(app):
        yield app, pool, model, runtime


@pytest.fixture
async def live(configured):
    app, pool, model, _runtime = configured
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, pool, model


@pytest.mark.parametrize("complete,status", [(False, "ask"), (True, "confirm")])
async def test_initial_clarify_is_201_and_does_not_create_run(live, complete, status):
    http, pool, model = live
    model.complete_brief = complete
    response = await http.post(
        "/research", json={"query": "Design an intrusion detector evaluation"}, headers=key()
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == status
    UUID(body["session_id"])
    assert body["brief_version"] == 1 and body["clarification_round"] == 0
    assert "sse_url" not in body
    assert model.calls == 1
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM messages") == 2
    view = (await http.get("/research/" + body["session_id"])).json()
    assert view["status"] == status and view["run_id"] is None


async def test_http_cancel_unfrozen_replays_without_new_model_or_run(live):
    http, pool, model = live
    initial = (
        await http.post("/research", json={"query": "Controlled lifecycle question"}, headers=key())
    ).json()
    path = f"/research/{initial['session_id']}/cancel"
    headers = key()
    first = await http.post(path, json={}, headers=headers)
    replay = await http.post(path, json={}, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert (
        first.json()
        == replay.json()
        == {"session_id": initial["session_id"], "status": "cancelled"}
    )
    assert model.calls == 1 and await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    assert (await http.get(path.removesuffix("/cancel"))).json()["status"] == "cancelled"


async def test_http_cancel_ready_reports_accepted_not_stopped(live):
    http, pool, _model = live
    initial = (
        await http.post("/research", json={"query": "Controlled lifecycle question"}, headers=key())
    ).json()
    path = f"/research/{initial['session_id']}"
    await http.post(path + "/confirm", json={"accepted": True, "brief_version": 1}, headers=key())
    result = await http.post(path + "/cancel", json={}, headers=key())
    assert result.status_code == 202 and result.json()["status"] == "cancelling"
    assert (await http.get(path)).json()["status"] == "cancelling"
    assert (await http.get(path + "/report")).status_code == 409
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_http_resume_replays_same_run_without_resetting_checkpoint(configured):
    app, pool, model, runtime = configured
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        initial = (
            await http.post(
                "/research", json={"query": "Controlled lifecycle question"}, headers=key()
            )
        ).json()
        path = f"/research/{initial['session_id']}"
        original = (
            await http.post(
                path + "/confirm", json={"accepted": True, "brief_version": 1}, headers=key()
            )
        ).json()
        async with runtime.repository_store.transaction() as tx:
            await runtime.repository_store.research.claim_run("test-worker", tx)
        await pool.execute(
            "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
        async with runtime.repository_store.transaction() as tx:
            await runtime.repository_store.research.scan_interrupted(tx)
        old = await http.post(path + "/resume", json={"checkpoint_seq": 2}, headers=key())
        assert old.status_code == 409 and old.json()["error"]["code"] == "stale_resource"
        headers = key()
        result = await http.post(path + "/resume", json={"checkpoint_seq": 1}, headers=headers)
        assert result.status_code == 202 and result.json() == original
        again = await http.post(path + "/resume", json={"checkpoint_seq": 1}, headers=headers)
        assert again.status_code == 202 and again.json() == original
        conflict = await http.post(path + "/resume", json={"checkpoint_seq": 1}, headers=key())
        assert (
            conflict.status_code == 409 and conflict.json()["error"]["code"] == "resume_not_allowed"
        )
        assert (await http.get(path)).json()["checkpoint_seq"] == 1
        assert (
            model.calls == 1 and await pool.fetchval("SELECT attempt_count FROM research_runs") == 1
        )


async def test_http_report_is_committed_markdown_and_completion_blocks_cancel(configured):
    from tests.integration.test_mono_report_publication import publish, reviewed

    app, _pool, model, runtime = configured
    commit, claimed, point, report = await reviewed(runtime.repository_store, DEVELOPMENT_USER_ID)
    await publish(runtime.repository_store, claimed, point)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        path = f"/research/{commit.session.session_id}"
        result = await http.get(path + "/report")
        assert result.status_code == 200
        assert result.json() == {
            "session_id": str(commit.session.session_id),
            "report_id": str(report.report_id),
            "version": 1,
            "review_verdict": "needs_more_work",
            "report": report.markdown,
            "references": [],
            "risks": [],
        }
        assert "\n" in result.json()["report"]
        view = (await http.get(path)).json()
        assert (
            view["status"] == "completed"
            and view["phase"] == "done"
            and view["checkpoint_seq"] == 3
        )
        cancelled = await http.post(path + "/cancel", json={}, headers=key())
        assert (
            cancelled.status_code == 409
            and cancelled.json()["error"]["code"] == "invalid_session_state"
        )
        assert model.calls == 0


async def test_http_cancel_cache_failure_rolls_back_resource(configured, monkeypatch):
    app, pool, _model, runtime = configured
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        initial = (
            await http.post(
                "/research", json={"query": "Controlled lifecycle question"}, headers=key()
            )
        ).json()
        path = f"/research/{initial['session_id']}"

        async def fail_cache(*args, **kwargs):
            await runtime.repository_store.connection(args[3]).execute("SELECT 1/0")

        headers = key()
        with monkeypatch.context() as patch:
            patch.setattr(runtime.repository_store.requests, "complete", fail_cache)
            result = await http.post(path + "/cancel", json={}, headers=headers)
        assert result.status_code == 503
        assert (await http.get(path)).json()["status"] == "confirm"
        assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
        assert (await http.post(path + "/cancel", json={}, headers=headers)).status_code == 200


async def test_idempotent_start_message_and_confirm(live):
    http, pool, model = live
    model.complete_brief = False
    headers = key()
    request = {"query": "Design an intrusion detector evaluation"}
    first = await http.post("/research", json=request, headers=headers)
    again = await http.post("/research", json=request, headers=headers)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert model.calls == 1
    conflict = await http.post("/research", json={"query": "Different research"}, headers=headers)
    assert conflict.status_code == 409
    session = first.json()["session_id"]
    model.complete_brief = True
    headers = key()
    request = {"content": "A reproducible public benchmark evaluation", "brief_version": 1}
    response = await http.post(f"/research/{session}/messages", json=request, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "confirm"
    assert response.json()["brief_version"] == 2
    replay = await http.post(f"/research/{session}/messages", json=request, headers=headers)
    assert replay.json() == response.json() and model.calls == 2
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    headers = key()
    confirmation = {"accepted": True, "brief_version": 2}
    response = await http.post(f"/research/{session}/confirm", json=confirmation, headers=headers)
    assert response.status_code == 202 and response.json()["status"] == "ready"
    for repeat_headers in (headers, key()):
        replay = await http.post(
            f"/research/{session}/confirm", json=confirmation, headers=repeat_headers
        )
        assert replay.status_code == 202 and replay.json() == response.json()
    assert model.calls == 2
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
    assert await pool.fetchval("SELECT count(*) FROM briefs WHERE frozen_at IS NOT NULL") == 1
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 1


async def test_stale_messages_and_concurrent_cas(live):
    http, pool, model = live
    model.complete_brief = False
    created = (
        await http.post("/research", json={"query": "Design evaluation"}, headers=key())
    ).json()
    session = created["session_id"]
    model.entered.clear()
    model.release = asyncio.Event()
    task = asyncio.create_task(
        http.post(
            f"/research/{session}/messages",
            json={"content": "first answer", "brief_version": 1},
            headers=key(),
        )
    )
    await model.entered.wait()
    other = asyncio.create_task(
        http.post(
            f"/research/{session}/messages",
            json={"content": "second answer", "brief_version": 1},
            headers=key(),
        )
    )
    try:
        await asyncio.wait_for(model.third_entered.wait(), timeout=5)
        assert model.calls == 3
    finally:
        model.release.set()
        responses = await asyncio.gather(task, other)
    assert sorted(response.status_code for response in responses) == [200, 409]
    stale = await http.post(
        f"/research/{session}/messages",
        json={"content": "old version", "brief_version": 1},
        headers=key(),
    )
    assert stale.status_code == 409
    assert await pool.fetchval("SELECT clarification_round FROM sessions") == 1
    assert await pool.fetchval("SELECT count(*) FROM messages") == 4


async def test_model_failure_has_no_half_session_and_key_can_retry(live):
    http, pool, model = live
    model.fail = True
    headers, body = key(), {"query": "Design evaluation"}
    failed = await http.post("/research", json=body, headers=headers)
    assert failed.status_code == 503
    assert "secret" not in failed.text
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
    assert (
        await pool.fetchval("SELECT count(*) FROM idempotency_requests WHERE state='completed'")
        == 0
    )
    model.fail = False
    retry = await http.post("/research", json=body, headers=headers)
    assert retry.status_code == 201


async def test_missing_key_unknown_owner_fields_and_illegal_confirmation(live):
    http, _pool, _model = live
    assert (await http.post("/research", json={"query": "question"})).status_code == 422
    assert (
        await http.post(
            "/research", json={"query": "question", "user_id": str(uuid4())}, headers=key()
        )
    ).status_code == 422
    created = (await http.post("/research", json={"query": "question"}, headers=key())).json()
    response = await http.post(
        f"/research/{created['session_id']}/confirm",
        json={"accepted": True, "brief_version": 1, "feedback": "change it"},
        headers=key(),
    )
    assert response.status_code == 422
    response = await http.post(
        f"/research/{uuid4()}/messages",
        json={"content": "answer", "brief_version": 1},
        headers=key(),
    )
    assert response.status_code == 404


@pytest.mark.parametrize("table", ["research_runs", "phase_snapshots"])
async def test_confirmation_sql_fault_rolls_back_freeze_and_cached_response(
    live, monkeypatch, table
):
    http, pool, model = live
    session = (
        await http.post("/research", json={"query": "Design evaluation"}, headers=key())
    ).json()["session_id"]
    original = research_postgres.insert

    async def inject(conn, name, data, suffix=""):
        if name == table:
            await conn.execute("SELECT 1/0")
        return await original(conn, name, data, suffix)

    headers, body = key(), {"accepted": True, "brief_version": 1}
    with monkeypatch.context() as patch:
        patch.setattr(research_postgres, "insert", inject)
        response = await http.post(f"/research/{session}/confirm", json=body, headers=headers)
        assert response.status_code == 503
    assert await pool.fetchval("SELECT status FROM sessions") == "confirm"
    assert await pool.fetchval("SELECT count(*) FROM briefs WHERE frozen_at IS NOT NULL") == 0
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 0
    response = await http.post(f"/research/{session}/confirm", json=body, headers=headers)
    assert response.status_code == 202
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert model.calls == 1


async def test_cross_owner_write_read_and_events_all_hide_resource(live):
    http, pool, model = live
    store = PostgresResearchStore(pool)
    other = User(
        user_id=uuid4(),
        email="other-owner@example.org",
        password_hash="fixture-hash",
        is_development=False,
        created_at=SystemClock().now_utc(),
    )
    change = candidate(other.user_id)
    async with store.transaction() as tx:
        await store.users.create(other, tx)
        await store.research.commit_session_change(0, change, tx)
    path = f"/research/{change.session.session_id}"
    for method, url, body in [
        ("GET", path, None),
        ("GET", path + "/report", None),
        ("GET", path + "/events", None),
        ("POST", path + "/messages", {"content": "answer", "brief_version": 1}),
        ("POST", path + "/confirm", {"accepted": True, "brief_version": 1}),
        ("POST", path + "/cancel", {}),
    ]:
        response = await http.request(method, url, json=body, headers=key())
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "session_not_found"
    assert model.calls == 0
    assert await pool.fetchval("SELECT revision FROM sessions") == 1


async def test_rejection_round_cap_and_explicit_patch_over_http(live):
    http, pool, model = live
    model.complete_brief = False
    created = (
        await http.post("/research", json={"query": "Design evaluation"}, headers=key())
    ).json()
    session = created["session_id"]
    version = 1
    for _ in range(4):
        response = await http.post(
            f"/research/{session}/messages",
            json={"content": "unclear answer", "brief_version": version},
            headers=key(),
        )
        assert response.status_code == 200 and response.json()["status"] == "ask"
        version = response.json()["brief_version"]
    assert model.calls == 4
    response = await http.post(
        f"/research/{session}/messages",
        json={"content": "Explicit fields", "brief_version": version, "brief_patch": core()},
        headers=key(),
    )
    assert response.json()["status"] == "confirm"
    version = response.json()["brief_version"]
    before = model.calls
    response = await http.post(
        f"/research/{session}/confirm",
        json={"accepted": False, "brief_version": version, "feedback": "Change the scope"},
        headers=key(),
    )
    assert response.status_code == 200 and response.json()["status"] == "ask"
    assert response.json()["brief_version"] == version + 1
    assert model.calls == before
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0


async def test_duplicate_inflight_request_does_not_make_another_model_call(live):
    http, pool, model = live
    model.release = asyncio.Event()
    headers, body = key(), {"query": "Design evaluation"}
    task = asyncio.create_task(http.post("/research", json=body, headers=headers))
    await model.entered.wait()
    try:
        response = await http.post("/research", json=body, headers=headers)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "request_in_progress"
        assert int(response.headers["Retry-After"]) > 0
        assert model.calls == 1
        assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
    finally:
        model.release.set()
        await task


async def test_parallel_confirmations_with_distinct_keys_accept_one_run(live):
    http, pool, model = live
    created = (await http.post("/research", json={"query": "evaluation"}, headers=key())).json()
    responses = await asyncio.gather(
        *[
            http.post(
                f"/research/{created['session_id']}/confirm",
                json={"accepted": True, "brief_version": 1},
                headers=key(),
            )
            for _ in range(2)
        ]
    )
    assert all(response.status_code == 202 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
    assert await pool.fetchval("SELECT count(*) FROM messages WHERE kind='confirmation'") == 1
    assert model.calls == 1


@pytest.mark.parametrize("fault", [False, True])
async def test_cli_frozen_intake_is_atomic_idempotent_and_has_no_model(
    configured, monkeypatch, fault
):
    _app, pool, model, runtime = configured
    brief = ResearchBrief.model_validate(
        assess_brief(
            PartialResearchBrief(), ClarifyAssessment(**assessment(brief_patch=core()))
        ).draft.model_dump()
    )
    original = research_postgres.insert

    async def inject(conn, name, data, suffix=""):
        if name == "phase_snapshots":
            await conn.execute("SELECT 1/0")
        return await original(conn, name, data, suffix)

    request_key = str(uuid4())
    if fault:
        with monkeypatch.context() as patch:
            patch.setattr(research_postgres, "insert", inject)
            with pytest.raises(AdapterError):
                await runtime.research.start_frozen(
                    DEVELOPMENT_USER_ID, brief, SourceSelection(), request_key
                )
        for table in ("sessions", "briefs", "messages", "research_runs", "phase_snapshots"):
            assert await pool.fetchval(f"SELECT count(*) FROM {table}") == 0
    first = await runtime.research.start_frozen(
        DEVELOPMENT_USER_ID, brief, SourceSelection(), request_key
    )
    replay = await runtime.research.start_frozen(
        DEVELOPMENT_USER_ID, brief, SourceSelection(), request_key
    )
    assert first == replay and first.status_code == 202
    assert model.calls == 0
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 1
    assert await pool.fetchval("SELECT count(*) FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == 1
    assert "cli" in await pool.fetchval("SELECT content FROM messages WHERE kind='confirmation'")
    assert await pool.fetchval("SELECT confirmed_by FROM briefs") == DEVELOPMENT_USER_ID


@pytest.mark.parametrize("failure", [False, True])
async def test_request_lease_renewal_and_failure_abort_inflight_model(
    configured, live, monkeypatch, failure
):
    _app, pool, model, runtime = configured
    http, _, _ = live
    runtime.research.renewal_interval_s = 0.01
    original = runtime.research.requests.renew
    renewed = asyncio.Event()

    async def renew(reservation, tx):
        if failure:
            renewed.set()
            raise AppError("service_not_ready", "Lease renewal unavailable", retryable=True)
        result = await original(reservation, tx)
        assert result.lease_expires_at > reservation.lease_expires_at
        renewed.set()
        return result

    monkeypatch.setattr(runtime.research.requests, "renew", renew)
    model.release = asyncio.Event()
    headers, body = key(), {"query": "evaluation"}
    task = asyncio.create_task(http.post("/research", json=body, headers=headers))
    try:
        await asyncio.wait_for(renewed.wait(), timeout=5)
        if failure:
            response = await asyncio.wait_for(task, timeout=5)
            assert response.status_code == 503
            assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
            assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 0
        else:
            response = await http.post("/research", json=body, headers=headers)
            assert response.status_code == 409 and model.calls == 1
    finally:
        model.release.set()
        await task
    if not failure:
        assert task.result().status_code == 201


async def test_cancelled_http_request_releases_key_without_half_session(live):
    http, pool, model = live
    model.release = asyncio.Event()
    headers, body = key(), {"query": "evaluation"}
    task = asyncio.create_task(http.post("/research", json=body, headers=headers))
    await asyncio.wait_for(model.entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 0
    model.release.set()
    assert (await http.post("/research", json=body, headers=headers)).status_code == 201


@pytest.mark.parametrize(
    "body,code,status",
    [
        ({"query": "evaluation", "task_type": "reviewer_response"}, "unsupported_task_type", 422),
        ({"query": " "}, "validation_error", 422),
        ({"query": []}, "validation_error", 422),
        ({"query": "evaluation", "sources": ["knowledge_base"]}, "validation_error", 422),
        (
            {
                "query": "evaluation",
                "sources": ["knowledge_base"],
                "knowledge_base_ids": [str(uuid4())],
            },
            "knowledge_base_not_found",
            404,
        ),
    ],
)
async def test_invalid_inputs_or_unknown_sources_do_not_call_model(live, body, code, status):
    http, pool, model = live
    response = await http.post("/research", json=body, headers=key())
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert model.calls == 0
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
