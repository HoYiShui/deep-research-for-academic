"""Owner-scoped HTTP attachments backed by real isolated PG and private MinIO."""

import asyncio
import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from application.research_artifacts import ResearchArtifacts
from application.research_queries import ResearchQueries
from application.settings import Settings
from domain.research.ids import canonical_hash
from domain.research.state import Checkpoint, PipelineState
from infrastructure.storage.artifacts import MinioArtifactStore
from interface.deps import require_user
from interface.main import create_app
from tests.integration.test_mono_run_lifecycle import checkpoint, claim, ready
from tests.integration.test_mono_transactions import setup_store


@pytest_asyncio.fixture
async def attachment_http(pg_database, object_cache, tmp_path):
    _, store, owner = await setup_store(pg_database)
    commit = await ready(store, owner.user_id)
    claimed = await claim(store, str(uuid4()))
    settings = Settings.load()
    blobs = MinioArtifactStore(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        object_cache.bucket,
        secure=settings.minio_secure,
    )
    body = b"value\n3\n"
    key = f"analysis/{commit.run.run_id}/a1/{hashlib.sha256(body).hexdigest()}/result.csv"
    await blobs.put(key, body)
    point = checkpoint(commit, 2, phase="analyze")
    data = point.state.model_dump(mode="json")
    data["comparison_sets"] = {
        "g1": {
            "comparison_set_id": "g1",
            "section_id": "section_1",
            "requirement_id": "r1",
            "metric_ids": [],
            "required_context_fields": [],
            "comparability": "incompatible",
            "reasons": ["Transport-only fixture; no research-quality claim"],
        }
    }
    data["analysis_artifacts"] = {
        "a1": {
            "artifact_id": "a1",
            "section_id": "section_1",
            "comparison_set_id": "g1",
            "input_metric_ids": [],
            "input_evidence_ids": [],
            "operation": "comparison_matrix",
            "code_or_recipe": "Transport fixture",
            "template_version": "fixture",
            "output": {"columns": ["value"], "rows": []},
            "object_keys": [key],
            "execution_status": "completed",
            "failure": None,
        }
    }
    state = PipelineState.model_validate(data)
    point = Checkpoint.model_validate(
        point.model_dump() | {"state": state, "state_hash": canonical_hash(state)}
    )
    async with store.transaction() as tx:
        await store.research.commit_checkpoint(claimed, 1, point, tx)

    async def close_runtime():
        pass

    runtime = SimpleNamespace(
        research_artifacts=ResearchArtifacts(ResearchQueries(store, store.research), blobs),
        aclose=close_runtime,
    )
    app = create_app(
        settings=Settings.load(env_file=tmp_path / "missing", environ={}),
        container_factory=lambda config: runtime,
    )
    app.dependency_overrides[require_user] = lambda: str(owner.user_id)
    app.state.attachment_fixture = (store, claimed, point)
    url = f"/research/{commit.session.session_id}/artifacts/a1/files/result.csv"
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as http,
        ):
            yield http, app, blobs, key, body, url
    finally:
        await blobs.close()


async def test_download_returns_private_bytes_not_storage_metadata(attachment_http):
    http, _, _, key, body, url = attachment_http
    response = await http.get(url)
    assert response.status_code == 200 and response.content == body
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="result.csv"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert key not in str(response.headers) and b"minio" not in response.content


async def test_cross_owner_denied_before_storage_io(attachment_http, monkeypatch):
    http, app, blobs, _, _, url = attachment_http
    app.dependency_overrides[require_user] = lambda: str(uuid4())

    async def forbidden(key):
        raise AssertionError("Unauthorized request reached MinIO")

    monkeypatch.setattr(blobs, "read", forbidden)
    response = await http.get(url)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "tail",
    ["unknown.csv", "payload.html", "..%5Csecret.csv", "%2Fsecret.csv", "result.csv%0D%0Ax-bad:y"],
)
async def test_unregistered_and_unsafe_paths_are_404(attachment_http, tail):
    http, _, _, _, _, url = attachment_http
    assert (await http.get(url.rsplit("/", 1)[0] + "/" + tail)).status_code == 404
    assert (await http.get(url.replace("/a1/", "/missing/"))).status_code == 404


@pytest.mark.parametrize("fault", ["missing", "corrupt", "oversize"])
async def test_unreadable_bytes_are_sanitized_503(attachment_http, fault):
    http, _, blobs, key, _, url = attachment_http
    if fault == "missing":
        await asyncio.to_thread(blobs._client.remove_object, blobs.bucket, key)
    else:
        value = b"private corrupt bytes" if fault == "corrupt" else b"x" * (blobs.max_bytes + 1)
        await asyncio.to_thread(
            blobs._client.put_object, blobs.bucket, key, io.BytesIO(value), len(value)
        )
    response = await http.get(url)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "content_unavailable"
    assert key not in response.text and "private corrupt" not in response.text


@pytest.mark.parametrize("fault", ["other_run", "other_artifact", "skipped"])
async def test_registered_attachment_still_requires_scope_and_completed_status(
    attachment_http, monkeypatch, fault
):
    http, app, blobs, key, _, url = attachment_http
    store, claimed, point = app.state.attachment_fixture
    current = await store.research.get_run(claimed.owner_id, claimed.run.run_id)
    claimed = type(claimed).model_validate(claimed.model_dump() | {"run": current})
    data = point.state.model_dump(mode="json")
    artifact = data["analysis_artifacts"]["a1"]
    if fault == "other_run":
        artifact["object_keys"] = [key.replace(str(current.run_id), str(uuid4()))]
    elif fault == "other_artifact":
        artifact["object_keys"] = [key.replace("/a1/", "/a2/")]
    else:
        artifact.update(execution_status="skipped", output={})
    state = PipelineState.model_validate(data)
    next_point = Checkpoint.model_validate(
        point.model_dump()
        | {
            "snapshot_id": uuid4(),
            "seq": 3,
            "state": state,
            "state_hash": canonical_hash(state),
        }
    )
    async with store.transaction() as tx:
        await store.research.commit_checkpoint(claimed, 2, next_point, tx)

    async def forbidden(key):
        raise AssertionError("Invalid scope/status reached MinIO")

    monkeypatch.setattr(blobs, "read", forbidden)
    response = await http.get(url)
    assert response.status_code == (404 if fault == "skipped" else 503)


async def test_attachment_store_rejects_mutable_keys_and_verifies_bytes(attachment_http):
    _, _, blobs, key, body, _ = attachment_http
    await asyncio.gather(blobs.put(key, body), blobs.put(key, body))
    assert await blobs.read(key) == body
    with pytest.raises(ValueError, match="hash"):
        await blobs.put(key, b"changed")
    for invalid in (
        "result.csv",
        key.replace("result.csv", "../result.csv"),
        key.replace("result.csv", "payload.html"),
    ):
        with pytest.raises(ValueError):
            await blobs.read(invalid)
