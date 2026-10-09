"""Canonical HTTP management using real PG/Standalone; no Parser/BGE claim."""

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from application.document_ingestion import DocumentIngestionService
from application.errors import AppError
from application.knowledge_base_management import KnowledgeBaseManagementService
from application.settings import Settings
from domain.ports import AdapterError
from infrastructure.clock import SystemClock
from infrastructure.vector.index import MilvusIndex
from interface.deps import require_user
from interface.main import create_app
from tests.integration.test_mono_document_content import content_store as shared_content_store
from tests.integration.test_mono_kb_lifecycle import submit
from tests.integration.test_mono_transactions import setup_store
from tests.knowledge_fixtures import submission

content_store = shared_content_store


@pytest_asyncio.fixture
async def kb_http(pg_database, content_store, tmp_path):
    pool, store, user = await setup_store(pg_database)
    owner = user.user_id
    settings = Settings.load()
    index = MilvusIndex(settings.milvus_uri)
    ingestion = DocumentIngestionService(store, store.knowledge, store.requests, content_store)
    management = KnowledgeBaseManagementService(
        store,
        store.knowledge,
        store.requests,
        index,
        SystemClock(),
        index_version=settings.index_version,
        ingestion=ingestion,
    )

    async def close():
        pass

    app = create_app(
        settings=Settings.load(env_file=tmp_path / "missing", environ={}),
        container_factory=lambda config: SimpleNamespace(
            knowledge_management=management, aclose=close
        ),
    )
    app.dependency_overrides[require_user] = lambda: str(owner)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as http,
        ):
            yield http, app, pool, store, owner, index, management
    finally:
        try:
            for identity in await pool.fetch(
                "SELECT kb_id FROM knowledge_bases WHERE owner_id=$1", owner
            ):
                await index.drop_partition(identity["kb_id"])
        finally:
            await index.close()


async def create(http, name="Private reference", key=None):
    response = await http.post(
        "/knowledge-bases", json={"name": name}, headers={"Idempotency-Key": key or uuid4().hex}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_creation_requires_real_partition_and_replays_original_public_view(
    kb_http, monkeypatch
):
    http, _, pool, store, owner, index, _ = kb_http
    view = await create(http, key="creation")
    assert view["status"] == "active" and view["data_classification"] == "private"
    assert view["revision"] == 2
    kb = await store.knowledge.get_kb(owner, UUID(view["kb_id"]))
    assert kb.lease_owner is None and kb.lease_token == 1
    # The real client has created a real UUID partition, not a mocked verification flag.
    from infrastructure.vector.index import COLLECTION, partition

    assert await index._call(
        "verify", lambda: index._client().has_partition(COLLECTION, partition(kb.kb_id))
    )
    for secret in ("lease_owner", "lease_token", "lease_expires_at", "cleanup_cursor", "owner_id"):
        assert secret not in view

    async def forbidden(*args):
        raise AssertionError("Replay must not access the external index")

    monkeypatch.setattr(index, "ensure_schema", forbidden)
    assert await create(http, key="creation") == view
    assert (await http.get(f"/knowledge-bases/{kb.kb_id}")).json() == view
    assert await pool.fetchval("SELECT count(*) FROM knowledge_bases") == 1


async def test_transient_index_failure_preserves_resource_for_same_key_retry(kb_http, monkeypatch):
    http, _, pool, store, owner, index, _ = kb_http
    ensure = index.ensure_partition

    async def unavailable(*args):
        raise AdapterError("vector", "index_not_ready", "Private SDK diagnostic", True, "create")

    monkeypatch.setattr(index, "ensure_partition", unavailable)
    headers = {"Idempotency-Key": "retry-creation"}
    failed = await http.post("/knowledge-bases", json={"name": "Retry"}, headers=headers)
    assert failed.status_code == 503
    assert "Private SDK diagnostic" not in failed.text
    identity = UUID(failed.json()["error"]["details"]["kb_id"])
    kb = await store.knowledge.get_kb(owner, identity)
    assert kb.status == "creating" and kb.lease_owner is None
    assert kb.failure.code == "index_not_ready" and kb.failure.details is None
    bound = await pool.fetchrow("SELECT * FROM idempotency_requests")
    assert bound["resource_id"] == identity and bound["state"] == "in_progress"
    monkeypatch.setattr(index, "ensure_partition", ensure)
    resumed = await http.post("/knowledge-bases", json={"name": "Retry"}, headers=headers)
    assert resumed.status_code == 201 and resumed.json()["kb_id"] == str(identity)
    assert await pool.fetchval("SELECT count(*) FROM knowledge_bases") == 1
    assert (await store.knowledge.get_kb(owner, identity)).lease_token == 2


async def test_creation_conflicts_and_patch_cas_idempotency(kb_http):
    http, _, pool, _, _, _, _ = kb_http
    first = await create(http, key="same")
    conflict = await http.post(
        "/knowledge-bases", json={"name": "Different"}, headers={"Idempotency-Key": "same"}
    )
    assert (
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    )
    duplicate = await http.post(
        "/knowledge-bases", json={"name": first["name"]}, headers={"Idempotency-Key": "other"}
    )
    assert (
        duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "name_already_exists"
    )
    url = f"/knowledge-bases/{first['kb_id']}"
    headers = {"Idempotency-Key": "patch"}
    body = {"revision": first["revision"], "description": "note"}
    response = await http.patch(url, json=body, headers=headers)
    assert response.status_code == 200 and response.json()["revision"] == first["revision"] + 1
    assert (await http.patch(url, json=body, headers=headers)).json() == response.json()
    stale = await http.patch(url, json=body, headers={"Idempotency-Key": "stale"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_resource"
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 2
    # Null clears; omitted description is not the same normalized request.
    cleared = await http.patch(
        url,
        json={"revision": response.json()["revision"], "description": None},
        headers={"Idempotency-Key": "clear"},
    )
    assert cleared.status_code == 200 and cleared.json()["description"] is None
    for invalid in (
        {"revision": 1},
        {"revision": 1, "data_classification": "public"},
        {"revision": True, "name": "changed"},
    ):
        rejected = await http.patch(url, json=invalid, headers={"Idempotency-Key": "invalid"})
        assert rejected.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"name": "  "},
        {"name": True},
        {"name": "n", "owner_id": str(uuid4())},
        {"name": "n", "data_classification": "internal"},
        {"name": "n", "index_version": "caller-profile"},
    ],
)
async def test_invalid_creation_does_not_write_pg_or_call_index(kb_http, body, monkeypatch):
    http, _, pool, _, _, index, _ = kb_http

    async def forbidden(*args):
        raise AssertionError("Invalid request must not call index")

    monkeypatch.setattr(index, "ensure_schema", forbidden)
    response = await http.post(
        "/knowledge-bases", json=body, headers={"Idempotency-Key": "invalid"}
    )
    assert response.status_code == 422
    assert await pool.fetchval("SELECT count(*) FROM knowledge_bases") == 0
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == 0


async def test_normalized_creation_body_and_deleted_tombstone_read(kb_http):
    http, _, _, store, owner, _, _ = kb_http
    from tests.knowledge_fixtures import knowledge_base

    headers = {"Idempotency-Key": "normalized"}
    first = await http.post("/knowledge-bases", json={"name": "  Trimmed  "}, headers=headers)
    assert first.status_code == 201 and first.json()["name"] == "Trimmed"
    assert (
        await http.post(
            "/knowledge-bases",
            json={"name": "Trimmed", "description": None, "data_classification": "private"},
            headers=headers,
        )
    ).json() == first.json()
    tombstone = knowledge_base(owner, status="deleted")
    # Read-policy fixture only: no claim that a cleanup worker deleted this KB.
    async with store.transaction() as tx:
        await store.knowledge.create_kb(tombstone, tx)
    assert (await http.get(f"/knowledge-bases/{tombstone.kb_id}")).json()["status"] == "deleted"
    assert len((await http.get("/knowledge-bases")).json()["items"]) == 1
    deleted = await http.get("/knowledge-bases", params={"status": "deleted"})
    assert [item["kb_id"] for item in deleted.json()["items"]] == [str(tombstone.kb_id)]


async def test_prepare_for_scoped_cli_does_not_start_global_creation_scanner(kb_http):
    _, _, _, store, _, _, _ = kb_http
    from application.bootstrap import HttpRuntime
    from tests.support.kb_http_server import NoModel

    runtime = HttpRuntime(
        settings=Settings.load().model_copy(update={"dr4a_debug_runner": False}),
        research_store=store,
        llm=NoModel(),
    )
    try:
        await runtime.prepare(start_runner=False)
        assert runtime.runner is None
        assert isinstance(runtime.knowledge_management, KnowledgeBaseManagementService)
    finally:
        await runtime.aclose()


async def test_owner_and_parent_guards_before_external_io(kb_http, monkeypatch):
    http, app, _, store, owner, index, _ = kb_http
    first = await create(http)
    kb_id = UUID(first["kb_id"])
    values = submission(await store.knowledge.get_kb(owner, kb_id))
    await submit(store, owner, values)
    second = await create(http, name="Other parent")
    wrong_parent = f"/knowledge-bases/{second['kb_id']}/documents/{values[0].document_id}"
    assert (await http.get(wrong_parent)).status_code == 404
    assert (await http.get(wrong_parent + "/versions")).status_code == 404

    async def forbidden(*args):
        raise AssertionError("Other owner's request must not access index")

    monkeypatch.setattr(index, "ensure_schema", forbidden)
    app.dependency_overrides[require_user] = lambda: str(uuid4())
    url = f"/knowledge-bases/{kb_id}"
    for response in (
        await http.get(url),
        await http.get(url + "/documents"),
        await http.patch(
            url, json={"revision": 2, "name": "leak"}, headers={"Idempotency-Key": "other"}
        ),
        await http.delete(url, headers={"Idempotency-Key": "other"}),
    ):
        assert response.status_code == 404
    assert (await http.get("/knowledge-bases")).json() == {"items": [], "next_cursor": None}


async def test_stable_equal_timestamp_paging_and_cursor_scope(kb_http):
    http, app, pool, _, owner, _, _ = kb_http
    expected = [await create(http, name=str(i)) for i in range(3)]
    # Tie-breaking must use UUID, not just the timestamp.
    await pool.execute("UPDATE knowledge_bases SET created_at='2026-01-01T00:00:00Z'")
    cursor, seen = None, []
    while True:
        response = await http.get(
            "/knowledge-bases", params={"limit": 1, **({"cursor": cursor} if cursor else {})}
        )
        assert response.status_code == 200
        page = response.json()
        seen.extend(item["kb_id"] for item in page["items"])
        if cursor is None:
            first_cursor = page["next_cursor"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(item["kb_id"] for item in expected)
    assert (
        await http.get("/knowledge-bases", params={"cursor": first_cursor, "status": "active"})
    ).status_code == 422
    app.dependency_overrides[require_user] = lambda: str(uuid4())
    assert (await http.get("/knowledge-bases", params={"cursor": first_cursor})).status_code == 422
    app.dependency_overrides[require_user] = lambda: str(owner)
    for invalid in (
        {"cursor": "bad?!"},
        {"cursor": "e30="},
        {"limit": 0},
        {"limit": 101},
        {"status": "fake"},
    ):
        assert (await http.get("/knowledge-bases", params=invalid)).status_code == 422


async def test_document_versions_job_views_and_no_internal_fields(kb_http):
    http, _, _, store, owner, _, _ = kb_http
    created = await create(http)
    kb = await store.knowledge.get_kb(owner, UUID(created["kb_id"]))
    first = submission(kb)
    await submit(store, owner, first)
    # Distinct documents rather than making a second active job for the same Document.
    second = submission(kb, body=b"second transport fixture")
    await submit(store, owner, second)
    url = f"/knowledge-bases/{kb.kb_id}/documents"
    page = (await http.get(url, params={"limit": 1})).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    next_page = (await http.get(url, params={"limit": 1, "cursor": page["next_cursor"]})).json()
    assert next_page["items"][0]["document_id"] != page["items"][0]["document_id"]
    detail = await http.get(url + f"/{first[0].document_id}")
    assert detail.status_code == 200
    assert detail.json()["latest_job"]["job_id"] == str(first[2].job_id)
    assert detail.json()["latest_job"]["retry_allowed"] is False
    assert detail.json()["versions"][0]["status"] == "staging"
    for secret in (
        "lease_owner",
        "lease_token",
        "source_object_key",
        "manifest_object_key",
        "parsed_object_key",
        "cleanup_cursor",
    ):
        assert secret not in detail.text
    versions = await http.get(url + f"/{first[0].document_id}/versions")
    assert versions.json()["items"] == detail.json()["versions"]
    assert (
        await http.get(
            url + f"/{first[0].document_id}/versions", params={"cursor": page["next_cursor"]}
        )
    ).status_code == 422


async def test_pending_delete_is_not_falsely_accepted_or_legacy_writable(kb_http):
    http, _, _, store, owner, _, _ = kb_http
    value = await create(http)
    response = await http.delete(
        f"/knowledge-bases/{value['kb_id']}", headers={"Idempotency-Key": "delete"}
    )
    assert response.status_code == 503 and response.json()["error"]["code"] == "service_not_ready"
    assert (await store.knowledge.get_kb(owner, UUID(value["kb_id"]))).status == "active"
    assert (await http.delete(f"/knowledge-bases/{value['kb_id']}")).status_code == 422
    assert (
        await http.request(
            "DELETE",
            f"/knowledge-bases/{value['kb_id']}",
            json={},
            headers={"Idempotency-Key": "body"},
        )
    ).status_code == 422
    assert (await http.get("/knowledge-base/documents")).status_code == 404
    assert (await http.post("/knowledge-base/search", json={"query": "q"})).status_code == 404


async def test_request_resource_binding_is_fenced_and_preserved(kb_http):
    _, _, pool, store, owner, _, _ = kb_http
    from domain.research.ids import canonical_hash

    identity = uuid4()
    async with store.transaction() as tx:
        reserved = await store.requests.reserve(owner, "binding", "key", canonical_hash({}), tx)
        bound = await store.requests.bind_resource(reserved, identity, tx)
    with pytest.raises(AppError, match="binding changed"):
        async with store.transaction() as tx:
            await store.requests.bind_resource(bound, uuid4(), tx)
    with pytest.raises(AppError, match="binding changed"):
        async with store.transaction() as tx:
            await store.requests.complete(bound, 201, {}, tx, resource_id=uuid4())
    with pytest.raises(AppError, match="no longer owned"):
        async with store.transaction() as tx:
            await store.requests.release(bound, tx)  # Bound identities cannot be forgotten.
    async with store.transaction() as tx:
        await store.requests.release(bound, tx, preserve_resource=True)
    async with store.transaction() as tx:
        newer = await store.requests.reserve(owner, "binding", "key", canonical_hash({}), tx)
    assert newer.resource_id == identity and newer.lease_expires_at != bound.lease_expires_at
    for operation in (store.requests.release,):
        with pytest.raises(AppError, match="no longer owned"):
            async with store.transaction() as tx:
                await operation(bound, tx)
    assert await pool.fetchval("SELECT resource_id FROM idempotency_requests") == identity


async def test_in_progress_creation_does_not_issue_concurrent_index_requests(kb_http, monkeypatch):
    http, _, pool, _, _, index, _ = kb_http
    reached, release = asyncio.Event(), asyncio.Event()
    original = index.ensure_partition

    async def pause(identity):
        reached.set()
        await release.wait()
        await original(identity)

    monkeypatch.setattr(index, "ensure_partition", pause)
    operation = asyncio.create_task(create(http, key="concurrent"))
    try:
        await asyncio.wait_for(reached.wait(), timeout=30)
        response = await http.post(
            "/knowledge-bases",
            json={"name": "Private reference"},
            headers={"Idempotency-Key": "concurrent"},
        )
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "request_in_progress"
        )
        assert int(response.headers["Retry-After"]) > 0
        assert await pool.fetchval("SELECT count(*) FROM knowledge_bases") == 1
    finally:
        release.set()
        await operation


async def test_creation_lease_heartbeat_and_loss_fences_publication(kb_http, monkeypatch):
    _, _, pool, store, owner, index, management = kb_http
    from application.knowledge_models import KnowledgeBaseCreate

    management.lease_s, management.heartbeat_s = 3, 0.05
    reached, release = asyncio.Event(), asyncio.Event()
    original = index.ensure_partition

    async def pause(identity):
        reached.set()
        await release.wait()
        await original(identity)

    monkeypatch.setattr(index, "ensure_partition", pause)
    operation = asyncio.create_task(
        management.create(owner, KnowledgeBaseCreate(name="fenced"), "fenced")
    )
    try:
        await asyncio.wait_for(reached.wait(), timeout=30)
        row = await pool.fetchrow("SELECT kb_id,lease_expires_at FROM knowledge_bases")
        async with asyncio.timeout(3):
            while (
                await pool.fetchval("SELECT lease_expires_at FROM knowledge_bases")
                <= row["lease_expires_at"]
            ):
                await asyncio.sleep(0.01)
        # Simulate the persisted fence changing; a heartbeat detects loss and
        # cancels the awaiting task rather than letting old work publish active.
        await pool.execute("UPDATE knowledge_bases SET lease_token=lease_token+1")
        with pytest.raises(AppError, match="lease.*current"):
            await asyncio.wait_for(operation, timeout=3)
        kb = await store.knowledge.get_kb(owner, row["kb_id"])
        assert kb.status == "creating" and kb.lease_token == 2
        assert await pool.fetchval("SELECT state FROM idempotency_requests") == "in_progress"
    finally:
        release.set()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def test_recovery_uses_pg_inventory_and_reuses_failed_creation_binding(kb_http, monkeypatch):
    http, _, pool, store, owner, index, management = kb_http
    original = index.ensure_partition

    async def unavailable(*args):
        raise AdapterError("vector", "index_not_ready", "private", True, "create")

    monkeypatch.setattr(index, "ensure_partition", unavailable)
    headers = {"Idempotency-Key": "background"}
    response = await http.post("/knowledge-bases", json={"name": "background"}, headers=headers)
    assert response.status_code == 503
    identity = UUID(response.json()["error"]["details"]["kb_id"])
    monkeypatch.setattr(index, "ensure_partition", original)
    await management.recover_creating()
    current = await store.knowledge.get_kb(owner, identity)
    assert current.status == "active" and current.failure is None and current.lease_owner is None
    # Recovery does not manufacture an HTTP response or wipe request history.
    assert await pool.fetchval("SELECT state FROM idempotency_requests") == "in_progress"
    resumed = await http.post("/knowledge-bases", json={"name": "background"}, headers=headers)
    assert resumed.status_code == 201 and resumed.json()["kb_id"] == str(identity)
    assert await pool.fetchval("SELECT count(*) FROM knowledge_bases") == 1


async def test_tcp_runtime_sigkill_after_partition_write_recovers_same_identity(
    pg_database, content_store
):
    import signal

    from infrastructure.vector.index import COLLECTION, partition
    from tests.integration.test_verify_clarify_http import server

    pool, database = pg_database
    index = MilvusIndex(Settings.load().milvus_uri)
    headers = {"Idempotency-Key": "crash-creation"}
    identities = []
    try:
        async with (
            server(
                database, kb_bucket=content_store.bucket, pause="kb_creation", with_process=True
            ) as (url, process),
            httpx.AsyncClient(base_url=url, timeout=30) as http,
        ):
            request = asyncio.create_task(
                http.post("/knowledge-bases", json={"name": "crash-safe"}, headers=headers)
            )
            try:
                marker = await asyncio.wait_for(process.stdout.readline(), timeout=30)
                assert marker == b"KB_PARTITION_WRITTEN\n"
                row = await pool.fetchrow("SELECT kb_id,status,lease_token FROM knowledge_bases")
                identities.append(row["kb_id"])
                assert row["status"] == "creating" and row["lease_token"] == 1
                assert await index._call(
                    "verify",
                    lambda: index._client().has_partition(COLLECTION, partition(row["kb_id"])),
                )
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=5)
                assert process.returncode == -signal.SIGKILL
                with pytest.raises(httpx.HTTPError):
                    await request
            finally:
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
        # Advance only the isolated test leases to model elapsed DB time without
        # a 120-second test sleep. The preceding interruption is an actual SIGKILL.
        await pool.execute(
            "UPDATE knowledge_bases SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
        await pool.execute(
            "UPDATE idempotency_requests SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
        async with (
            server(database, kb_bucket=content_store.bucket) as url,
            httpx.AsyncClient(base_url=url, timeout=30) as http,
        ):
            async with asyncio.timeout(15):
                while True:
                    view = await http.get(f"/knowledge-bases/{identities[0]}")
                    assert view.status_code == 200
                    if view.json()["status"] == "active":
                        break
                    await asyncio.sleep(0.05)
            resumed = await http.post(
                "/knowledge-bases", json={"name": "crash-safe"}, headers=headers
            )
            assert resumed.status_code == 201 and resumed.json()["kb_id"] == str(identities[0])
            assert (
                await http.post("/knowledge-bases", json={"name": "crash-safe"}, headers=headers)
            ).json() == resumed.json()
            patch = await http.patch(
                f"/knowledge-bases/{identities[0]}",
                json={"revision": resumed.json()["revision"], "description": "recovered"},
                headers={"Idempotency-Key": "after-restart"},
            )
            assert patch.status_code == 200
            assert (await http.get("/knowledge-bases")).json()["items"] == [patch.json()]
            assert (await http.get(f"/knowledge-bases/{identities[0]}/documents")).json() == {
                "items": [],
                "next_cursor": None,
            }
        assert await pool.fetchval("SELECT count(*) FROM knowledge_bases") == 1
        assert await pool.fetchval("SELECT lease_token FROM knowledge_bases") == 2
        assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 0
        assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
    finally:
        try:
            # Also catch an assertion failing before the crash marker/identity
            # was consumed. Every row in this database belongs to this test.
            identities = set(identities) | {
                row["kb_id"] for row in await pool.fetch("SELECT kb_id FROM knowledge_bases")
            }
            for identity in identities:
                await index.drop_partition(identity)
        finally:
            await index.close()
