"""Public job GET/retry via HTTP with real isolated PG and immutable MinIO source."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from application.settings import Settings
from domain.ports import AdapterError
from domain.research.models import Failure
from interface.deps import require_user
from interface.main import create_app
from tests.integration.test_mono_document_content import content_store as shared_content_store
from tests.integration.test_mono_kb_lifecycle import seeded, submit
from tests.knowledge_fixtures import submission

content_store = shared_content_store


@pytest_asyncio.fixture
async def job_http(pg_database, content_store, tmp_path):
    from application.document_ingestion import DocumentIngestionService

    pool, store, owner, kb = await seeded(pg_database)
    body = b"%PDF-1.4\nSource transport fixture; no parsed PDF claim.\n"
    values = submission(kb, body=body)
    await content_store.put(values[1].source_object_key, body, media_type="application/pdf")
    await submit(store, owner, values)
    async with store.transaction() as tx:
        held = await store.knowledge.claim_job(owner, values[2].job_id, "fixture", tx)
    failure = Failure(
        code="interrupted",
        dependency="worker",
        operation="ingest",
        phase=None,
        message="Controlled worker interruption",
        retryable=True,
        resume_allowed=True,
        attempt=1,
        occurred_at=datetime.now(UTC),
        details=None,
    )
    async with store.transaction() as tx:
        await store.knowledge.fail_job(owner, held.job_id, held.lease_token, failure, tx)
    service = DocumentIngestionService(store, store.knowledge, store.requests, content_store)

    async def close():
        pass

    app = create_app(
        settings=Settings.load(env_file=tmp_path / "missing", environ={}),
        container_factory=lambda config: SimpleNamespace(ingestion=service, aclose=close),
    )
    app.dependency_overrides[require_user] = lambda: str(owner)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        yield client, app, pool, store, owner, values, content_store


async def test_public_job_view_and_retry_replay_use_real_verified_source(job_http):
    http, _, pool, store, owner, values, blobs = job_http
    job_id = values[2].job_id
    url = f"/ingestion-jobs/{job_id}"
    response = await http.get(url)
    assert response.status_code == 200
    view = response.json()
    assert view["status"] == "failed" and view["retry_allowed"] is True
    assert view["kb_id"] == str(values[0].kb_id)
    assert view["document_id"] == str(values[0].document_id)
    for secret in (
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "source_object_key",
        values[1].source_object_key,
    ):
        assert secret not in response.text
    headers = {"Idempotency-Key": "fixture-retry"}
    response = await http.post(url + "/retry", json={}, headers=headers)
    assert response.status_code == 202 and response.json() == {
        "job_id": str(job_id),
        "status": "accepted",
    }
    assert (await store.knowledge.get_job(owner, job_id)).attempt_count == 1
    await blobs.delete(values[1].source_object_key)
    replay = await http.post(url + "/retry", json={}, headers=headers)
    assert replay.status_code == 202 and replay.json() == response.json()
    assert await pool.fetchval("SELECT count(*) FROM ingestion_jobs") == 1
    assert await pool.fetchval("SELECT count(*) FROM document_versions") == 1
    assert (
        await pool.fetchval("SELECT count(*) FROM idempotency_requests WHERE state='completed'")
        == 1
    )


async def test_other_owner_get_and_retry_never_touch_storage(job_http, monkeypatch):
    http, app, _, _, _, values, blobs = job_http
    app.dependency_overrides[require_user] = lambda: str(uuid4())

    async def forbidden(*args):
        raise AssertionError("Unauthorized storage I/O")

    monkeypatch.setattr(blobs, "head", forbidden)
    monkeypatch.setattr(blobs, "get", forbidden)
    url = f"/ingestion-jobs/{values[2].job_id}"
    assert (await http.get(url)).status_code == 404
    assert (
        await http.post(url + "/retry", json={}, headers={"Idempotency-Key": "other"})
    ).status_code == 404


async def test_missing_source_is_not_retryable_or_false_acceptance(job_http):
    http, _, _, store, owner, values, blobs = job_http
    await blobs.delete(values[1].source_object_key)
    url = f"/ingestion-jobs/{values[2].job_id}"
    assert (await http.get(url)).json()["retry_allowed"] is False
    response = await http.post(url + "/retry", json={}, headers={"Idempotency-Key": "missing"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "resume_not_allowed"
    assert (await store.knowledge.get_job(owner, values[2].job_id)).status == "failed"


async def test_cancel_without_cleanup_capability_is_explicitly_unavailable(job_http):
    http, _, _, store, owner, values, _ = job_http
    response = await http.post(
        f"/ingestion-jobs/{values[2].job_id}/cancel", json={}, headers={"Idempotency-Key": "cancel"}
    )
    assert response.status_code == 503 and response.json()["error"]["code"] == "service_not_ready"
    assert (await store.knowledge.get_job(owner, values[2].job_id)).status == "failed"


async def test_job_mutation_requires_empty_body_and_idempotency_key(job_http):
    http, _, _, _, _, values, _ = job_http
    url = f"/ingestion-jobs/{values[2].job_id}/retry"
    assert (await http.post(url, json={})).status_code == 422
    assert (
        await http.post(
            url, json={"source_object_key": "arbitrary"}, headers={"Idempotency-Key": "bad"}
        )
    ).status_code == 422


async def test_corrupt_source_fails_without_acceptance_or_leaked_key(job_http):
    import asyncio
    import io

    http, _, pool, store, owner, values, blobs = job_http
    key = values[1].source_object_key
    body = b"private corrupt fixture bytes"
    await asyncio.to_thread(
        blobs._client.put_object, blobs.bucket, key, io.BytesIO(body), len(body)
    )
    response = await http.post(
        f"/ingestion-jobs/{values[2].job_id}/retry", json={}, headers={"Idempotency-Key": "corrupt"}
    )
    assert response.status_code == 503 and response.json()["error"]["code"] == "content_unavailable"
    assert key not in response.text and body.decode() not in response.text
    assert (await store.knowledge.get_job(owner, values[2].job_id)).status == "failed"
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 0


async def test_storage_outage_is_not_missing_source_and_reservation_is_released(
    job_http, monkeypatch
):
    http, _, pool, _, _, values, blobs = job_http

    async def outage(key):
        raise AdapterError(
            "minio", "dependency_unavailable", "private provider canary", True, "head"
        )

    monkeypatch.setattr(blobs, "head", outage)
    url = f"/ingestion-jobs/{values[2].job_id}"
    for response in (
        await http.get(url),
        await http.post(url + "/retry", json={}, headers={"Idempotency-Key": "outage"}),
    ):
        assert response.status_code == 503 and "private provider canary" not in response.text
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 0


async def test_delete_barrier_during_source_io_wins_before_retry_commit(job_http, monkeypatch):
    http, _, pool, store, owner, values, blobs = job_http
    original = blobs.get

    async def with_barrier(key):
        body = await original(key)
        await pool.execute("UPDATE knowledge_bases SET status='deleting'")
        return body

    monkeypatch.setattr(blobs, "get", with_barrier)
    response = await http.post(
        f"/ingestion-jobs/{values[2].job_id}/retry",
        json={},
        headers={"Idempotency-Key": "delete-race"},
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "resource_not_active"
    assert (await store.knowledge.get_job(owner, values[2].job_id)).status == "failed"
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 0


@pytest.mark.parametrize(
    "corruption",
    [
        None,
        {"job_id": str(uuid4()), "status": "accepted"},
        {"job_id": "other", "status": "accepted", "secret": "canary"},
    ],
)
async def test_corrupt_cached_response_is_not_returned_as_acceptance(job_http, corruption):
    import json

    http, _, pool, _, _, values, _ = job_http
    url = f"/ingestion-jobs/{values[2].job_id}/retry"
    headers = {"Idempotency-Key": "cached"}
    assert (await http.post(url, json={}, headers=headers)).status_code == 202
    await pool.execute(
        "UPDATE idempotency_requests SET response_body=$1::jsonb", json.dumps(corruption)
    )
    response = await http.post(url, json={}, headers=headers)
    assert response.status_code == 503 and "canary" not in response.text
