"""Real PG/MinIO/Standalone deletion; controlled vectors, not Parser/BGE acceptance."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from application.bootstrap import HttpRuntime
from application.records import DEVELOPMENT_USER_ID
from application.settings import Settings
from interface.main import create_app
from tests.integration.test_mono_document_content import content_store as shared_content_store
from tests.integration.test_mono_kb_http import create
from tests.integration.test_mono_kb_lifecycle import activate, submit
from tests.integration.test_mono_milvus import row
from tests.integration.test_mono_transactions import setup_store
from tests.knowledge_fixtures import chunk, content_key, submission
from tests.support.kb_http_server import NoModel

content_store = shared_content_store


@pytest_asyncio.fixture
async def deleting_http(pg_database, content_store):
    pool, store, _ = await setup_store(pg_database)
    config = Settings.load().model_copy(
        update={
            "minio_bucket": content_store.bucket,
            "dr4a_debug_runner": False,
            "scan_s": 1,
            "dr4a_auth_required": False,
        }
    )
    runtime = HttpRuntime(settings=config, research_store=store, llm=NoModel())
    app = create_app(settings=config, container_factory=lambda settings: runtime)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as http,
        ):
            yield http, runtime, pool, store, DEVELOPMENT_USER_ID
    finally:
        # Child resources are gone before their own unique test database is removed.
        from infrastructure.vector.index import MilvusIndex

        cleanup = MilvusIndex(config.milvus_uri)
        try:
            for row in await pool.fetch("SELECT kb_id FROM knowledge_bases"):
                await cleanup.drop_partition(row["kb_id"])
        finally:
            await cleanup.close()


async def deleted(http, url):
    async with asyncio.timeout(10):
        while True:
            response = await http.get(url)
            assert response.status_code == 200
            body = response.json()
            value = body.get("document", body)
            if value["status"] == "deleted":
                return value
            await asyncio.sleep(0.02)


async def test_runtime_delete_is_accepted_then_physically_cleaned_with_tombstone(deleting_http):
    http, runtime, pool, _, owner = deleting_http
    kb = await create(http)
    identity = UUID(kb["kb_id"])
    from infrastructure.vector.index import COLLECTION, partition

    url = f"/knowledge-bases/{identity}"
    response = await http.delete(url, headers={"Idempotency-Key": "delete"})
    assert response.status_code == 202 and response.json()["status"] == "deleting"
    tombstone = await deleted(http, url)
    assert tombstone["kb_id"] == str(identity)
    assert not await runtime.knowledge_index._call(
        "verify",
        lambda: runtime.knowledge_index._client().has_partition(COLLECTION, partition(identity)),
    )
    assert (
        await pool.fetchval("SELECT owner_id FROM knowledge_bases WHERE kb_id=$1", identity)
        == owner
    )
    assert (await http.get("/knowledge-bases")).json()["items"] == []
    # Same key replays acceptance; new key reads the completed tombstone.
    assert (await http.delete(url, headers={"Idempotency-Key": "delete"})).json() == response.json()
    repeated = await http.delete(url, headers={"Idempotency-Key": "deleted-again"})
    assert repeated.status_code == 200 and repeated.json()["status"] == "deleted"


async def seed_content(runtime, store, owner, kb, *, document=None, body=b"source", publish=True):
    values = submission(kb, document, body=body)
    await runtime.knowledge_content.put(
        values[1].source_object_key, body, media_type="application/pdf"
    )
    await submit(store, owner, values, create=document is None)
    version = values[1]
    keys = [version.source_object_key]
    for blob in (b"parsed", b"manifest", b"Fixture chunk 0"):
        key = content_key(version, blob)
        await runtime.knowledge_content.put(key, blob, media_type="text/plain")
        keys.append(key)
    indexed = row(kb.kb_id, version.document_version_id, chunk(version).chunk_id).model_copy(
        update={
            "document_id": values[0].document_id,
            "index_version": kb.index_version,
        }
    )
    await runtime.knowledge_index.upsert([indexed])
    assert await runtime.knowledge_index.verify_rows([indexed])
    if publish:
        await activate(store, owner, values)
    return values, keys, indexed


async def missing_objects(content, keys):
    from domain.ports import AdapterError

    for key in keys:
        with pytest.raises(AdapterError, match="content_missing"):
            await content.head(key)


@pytest.mark.parametrize("kind", ["kb", "document"])
async def test_real_delete_all_versions_keeps_other_resources_and_completed_history(
    deleting_http, kind
):
    http, runtime, pool, store, owner = deleting_http
    view = await create(http)
    kb = await store.knowledge.get_kb(owner, UUID(view["kb_id"]))
    first, keys1, indexed1 = await seed_content(runtime, store, owner, kb)
    second, keys2, indexed2 = await seed_content(
        runtime, store, owner, kb, document=first[0], body=b"replacement"
    )
    pending, keys3, indexed3 = await seed_content(
        runtime, store, owner, kb, document=first[0], body=b"pending", publish=False
    )
    # Unrelated KB, and for document deletion an unrelated document in the SAME KB.
    other_view = await create(http, "Other KB")
    other_kb = await store.knowledge.get_kb(owner, UUID(other_view["kb_id"]))
    _other, other_keys, other_row = await seed_content(
        runtime, store, owner, other_kb, body=b"other"
    )
    sibling = None
    if kind == "document":
        sibling = await seed_content(runtime, store, owner, kb, body=b"sibling")
    research_body = b"Frozen research excerpt survives KB deletion"
    from hashlib import sha256

    research_key = f"research-content/{uuid4()}/{sha256(research_body).hexdigest()}"
    reference = await runtime.knowledge_content.put(
        research_key, research_body, media_type="text/plain"
    )
    completed_before = [
        await store.knowledge.get_job(owner, item[2].job_id) for item in (first, second)
    ]
    url = f"/knowledge-bases/{kb.kb_id}" + (
        f"/documents/{first[0].document_id}" if kind == "document" else ""
    )
    response = await http.delete(url, headers={"Idempotency-Key": "delete-versions"})
    assert response.status_code == 202, response.text
    await deleted(http, url)
    await missing_objects(runtime.knowledge_content, keys1 + keys2 + keys3)
    from domain.ports import AdapterError

    for indexed in (indexed1, indexed2, indexed3):
        with pytest.raises(AdapterError):
            await runtime.knowledge_index.verify_rows([indexed])
    assert await runtime.knowledge_index.verify_rows([other_row])
    for key in other_keys:
        assert await runtime.knowledge_content.head(key)
    if sibling:
        assert await runtime.knowledge_index.verify_rows([sibling[2]])
        for key in sibling[1]:
            assert await runtime.knowledge_content.head(key)
    assert await runtime.knowledge_content.read(reference) == research_body
    for item, expected in zip((first, second), completed_before, strict=True):
        assert await store.knowledge.get_job(owner, item[2].job_id) == expected
        assert (
            await pool.fetchval(
                "SELECT status FROM document_versions WHERE document_version_id=$1",
                item[1].document_version_id,
            )
            == "retired"
        )
    assert (await store.knowledge.get_job(owner, pending[2].job_id)).status == "cancelled"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id=$1", first[0].document_id
        )
        == 0
    )
    assert (
        await pool.fetchval(
            "SELECT active_version_id FROM documents WHERE document_id=$1", first[0].document_id
        )
        is None
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM document_versions WHERE document_id=$1", first[0].document_id
        )
        == 3
    )


@pytest.mark.parametrize("kind", ["kb", "document"])
async def test_deleted_tombstone_sweep_removes_late_writes_only_in_scope(deleting_http, kind):
    http, runtime, pool, store, owner = deleting_http
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    values, keys, indexed = await seed_content(runtime, store, owner, kb)
    url = f"/knowledge-bases/{kb.kb_id}" + (
        f"/documents/{values[0].document_id}" if kind == "document" else ""
    )
    assert (await http.delete(url, headers={"Idempotency-Key": "tombstone"})).status_code == 202
    tombstone = await deleted(http, url)
    await runtime.knowledge_index.ensure_partition(kb.kb_id)
    await runtime.knowledge_index.upsert([indexed])
    await runtime.knowledge_content.put(keys[0], b"source", media_type="application/pdf")
    assert await runtime.knowledge_index.verify_rows([indexed])
    table, identity, value = (
        ("documents", "document_id", values[0].document_id)
        if kind == "document"
        else ("knowledge_bases", "kb_id", kb.kb_id)
    )
    # Time advancement is confined to this invocation's isolated test rows.
    await pool.execute(
        f"UPDATE {table} SET cleanup_verified_at=clock_timestamp()-interval '2 minutes' WHERE {identity}=$1",
        value,
    )
    await runtime.knowledge_cleanup.tick()
    await missing_objects(runtime.knowledge_content, keys)
    from domain.ports import AdapterError

    with pytest.raises(AdapterError):
        await runtime.knowledge_index.verify_rows([indexed])
    view = (await http.get(url)).json()
    assert (
        view.get("document", view) == tombstone
    )  # Private GC timestamps/tokens do not change public revision.


async def test_dependency_failure_persists_cursor_and_recovery_completes(
    deleting_http, monkeypatch
):
    from domain.ports import AdapterError

    http, runtime, pool, store, owner = deleting_http
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    values, keys, _ = await seed_content(runtime, store, owner, kb)
    original = runtime.knowledge_content.delete_prefix
    failed = asyncio.Event()

    async def unavailable(prefix):
        failed.set()
        raise AdapterError(
            "minio", "dependency_unavailable", "SECRET SDK credentials", True, "delete"
        )

    monkeypatch.setattr(runtime.knowledge_content, "delete_prefix", unavailable)
    url = f"/knowledge-bases/{kb.kb_id}"
    accepted = await http.delete(url, headers={"Idempotency-Key": "failure"})
    assert accepted.status_code == 202
    await asyncio.wait_for(failed.wait(), 5)
    async with asyncio.timeout(5):
        while True:
            current = await store.knowledge.get_kb(owner, kb.kb_id)
            if current.failure is not None:
                break
            await asyncio.sleep(0.02)
    assert current.status == "deleting" and current.cleanup_cursor == "objects"
    assert "SECRET" not in (await http.get(url)).text
    assert current.lease_owner is not None  # No retry races an uncertain native I/O.
    assert (
        await pool.fetchval(
            "SELECT status FROM document_versions WHERE document_version_id=$1",
            values[1].document_version_id,
        )
        == "active"
    )
    assert await runtime.knowledge_content.head(keys[0])
    monkeypatch.setattr(runtime.knowledge_content, "delete_prefix", original)
    await pool.execute(
        "UPDATE knowledge_bases SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE kb_id=$1",
        kb.kb_id,
    )
    await runtime.knowledge_cleanup.tick()
    await deleted(http, url)
    await missing_objects(runtime.knowledge_content, keys)
    replay = await http.delete(url, headers={"Idempotency-Key": "failure"})
    assert replay.status_code == 202 and replay.json() == accepted.json()


async def test_deletion_waits_for_live_old_job_and_rejects_late_publication(deleting_http):
    from application.errors import AppError

    http, runtime, pool, store, owner = deleting_http
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    values, keys, _ = await seed_content(runtime, store, owner, kb, publish=False)
    async with store.transaction() as tx:
        old = await store.knowledge.claim_job(owner, values[2].job_id, "old-worker", tx)
    url = f"/knowledge-bases/{kb.kb_id}"
    assert (await http.delete(url, headers={"Idempotency-Key": "wait"})).status_code == 202
    await runtime.knowledge_cleanup.tick()
    assert (await store.knowledge.get_job(owner, old.job_id)).status == "cancelling"
    assert (await store.knowledge.get_kb(owner, kb.kb_id)).cleanup_cursor == "wait_jobs"
    assert await runtime.knowledge_content.head(keys[0])
    with pytest.raises(AppError):
        async with store.transaction() as tx:
            await store.knowledge.activate(
                owner, old.job_id, old.lease_token, [chunk(values[1])], keys[1], keys[2], tx
            )
    await pool.execute(
        "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=$1",
        old.job_id,
    )
    await pool.execute(
        "UPDATE knowledge_bases SET cleanup_not_before=clock_timestamp()-interval '1 second' WHERE kb_id=$1",
        kb.kb_id,
    )
    await runtime.knowledge_cleanup.tick()
    await deleted(http, url)
    assert (await store.knowledge.get_job(owner, old.job_id)).status == "cancelled"
    await missing_objects(runtime.knowledge_content, keys)


@pytest.mark.parametrize(
    "boundary,cursor", [("kb_delete_index", "index"), ("kb_delete_objects", "objects")]
)
async def test_real_tcp_sigkill_cleanup_restarts_from_committed_cursor(
    pg_database, content_store, boundary, cursor
):
    import signal
    from hashlib import sha256

    from infrastructure.vector.index import COLLECTION, MilvusIndex, partition
    from tests.integration.test_verify_clarify_http import server

    pool, database = pg_database
    index = MilvusIndex(Settings.load().milvus_uri)
    kb_id = None
    try:
        async with (
            server(database, kb_bucket=content_store.bucket, pause=boundary, with_process=True) as (
                url,
                process,
            ),
            httpx.AsyncClient(base_url=url, timeout=30) as http,
        ):
            kb_id = UUID((await create(http))["kb_id"])
            body = b"Cleanup crash boundary"
            key = f"knowledge-content/{DEVELOPMENT_USER_ID}/{kb_id}/{uuid4()}/{sha256(body).hexdigest()}"
            await content_store.put(key, body, media_type="text/plain")
            endpoint = f"/knowledge-bases/{kb_id}"
            accepted = await http.delete(endpoint, headers={"Idempotency-Key": "crash-delete"})
            assert accepted.status_code == 202
            assert await asyncio.wait_for(process.stdout.readline(), 30) == b"KB_DELETE_WRITTEN\n"
            value = await pool.fetchrow(
                "SELECT status,cleanup_cursor,lease_token FROM knowledge_bases WHERE kb_id=$1",
                kb_id,
            )
            assert value["status"] == "deleting" and value["cleanup_cursor"] == cursor
            assert not await index._call(
                "verify", lambda: index._client().has_partition(COLLECTION, partition(kb_id))
            )
            process.kill()
            await asyncio.wait_for(process.wait(), 5)
            assert process.returncode == -signal.SIGKILL
        await pool.execute(
            "UPDATE knowledge_bases SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE kb_id=$1",
            kb_id,
        )
        async with (
            server(database, kb_bucket=content_store.bucket) as url,
            httpx.AsyncClient(base_url=url, timeout=30) as http,
        ):
            await deleted(http, endpoint)
            replay = await http.delete(endpoint, headers={"Idempotency-Key": "crash-delete"})
            assert replay.status_code == 202 and replay.json() == accepted.json()
            await missing_objects(content_store, [key])
        assert (
            await pool.fetchval("SELECT lease_token FROM knowledge_bases WHERE kb_id=$1", kb_id)
            > value["lease_token"]
        )
    finally:
        try:
            if kb_id is not None:
                await index.drop_partition(kb_id)
        finally:
            await index.close()


async def test_cleanup_lost_heartbeat_stops_next_io_and_cannot_publish_tombstone(
    deleting_http, monkeypatch
):
    from application.errors import AppError

    http, runtime, pool, store, owner = deleting_http
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    _, keys, _ = await seed_content(runtime, store, owner, kb)
    runtime.knowledge_cleanup.heartbeat_s = 0.05
    entered, stopped = asyncio.Event(), asyncio.Event()
    original = runtime.knowledge_content.delete_prefix

    async def waiting(prefix):
        entered.set()
        try:
            await asyncio.Event().wait()
            await original(prefix)
        finally:
            stopped.set()

    async def lost(*args, **kwargs):
        raise AppError("stale_resource", "Controlled heartbeat loss")

    monkeypatch.setattr(runtime.knowledge_content, "delete_prefix", waiting)
    monkeypatch.setattr(store.knowledge, "renew_lifecycle", lost)
    url = f"/knowledge-bases/{kb.kb_id}"
    assert (await http.delete(url, headers={"Idempotency-Key": "lost"})).status_code == 202
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.wait_for(stopped.wait(), 5)
    assert (await store.knowledge.get_kb(owner, kb.kb_id)).status == "deleting"
    assert await runtime.knowledge_content.head(keys[0])
    monkeypatch.setattr(runtime.knowledge_content, "delete_prefix", original)
    await pool.execute(
        "UPDATE knowledge_bases SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE kb_id=$1",
        kb.kb_id,
    )
    # This recovery finishes before the next controlled heartbeat-loss callback.
    runtime.knowledge_cleanup.heartbeat_s = 20
    await runtime.knowledge_cleanup.tick()
    await deleted(http, url)


async def test_cleanup_heartbeat_keeps_live_ownership_during_slow_io(deleting_http, monkeypatch):
    from application.errors import AppError

    http, runtime, _pool, store, owner = deleting_http
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    _, keys, _ = await seed_content(runtime, store, owner, kb)
    runtime.knowledge_cleanup.heartbeat_s = 0.05
    runtime.knowledge_cleanup.lease_s = 1
    entered, gate = asyncio.Event(), asyncio.Event()
    original = runtime.knowledge_content.delete_prefix

    async def waiting(prefix):
        entered.set()
        await gate.wait()
        await original(prefix)

    monkeypatch.setattr(runtime.knowledge_content, "delete_prefix", waiting)
    url = f"/knowledge-bases/{kb.kb_id}"
    try:
        assert (await http.delete(url, headers={"Idempotency-Key": "heartbeat"})).status_code == 202
        await asyncio.wait_for(entered.wait(), 5)
        first = await store.knowledge.get_kb(owner, kb.kb_id)
        async with asyncio.timeout(3):
            while True:
                now = await store.knowledge.get_kb(owner, kb.kb_id)
                if now.lease_expires_at > first.lease_expires_at:
                    break
                await asyncio.sleep(0.02)
        async with store.transaction() as tx:
            with pytest.raises(AppError, match="Lifecycle worker"):
                await store.knowledge.claim_lifecycle(owner, kb.kb_id, "competing", tx)
        gate.set()
        await deleted(http, url)
        await missing_objects(runtime.knowledge_content, keys)
    finally:
        gate.set()


@pytest.mark.parametrize("kind", ["kb", "document"])
async def test_final_metadata_sql_clock_fence_rolls_back_partial_changes(
    deleting_http, monkeypatch, kind
):
    from application.errors import AppError

    http, runtime, pool, store, owner = deleting_http
    await runtime.runner.aclose()  # This test owns the metadata lease; no background competitor.
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    values, _, _ = await seed_content(runtime, store, owner, kb)
    document_id = values[0].document_id if kind == "document" else None
    # Drive only PG cursors here; the explicit verification flags are controlled,
    # not counted as physical deletion acceptance.
    async with store.transaction() as tx:
        if document_id:
            await store.knowledge.mark_document_deleting(owner, kb.kb_id, document_id, tx)
        else:
            await store.knowledge.mark_kb_deleting(owner, kb.kb_id, tx)
        held = await store.knowledge.claim_lifecycle(
            owner, kb.kb_id, "clock-test", tx, document_id=document_id, lease_s=1
        )
        for cursor in ("index", "objects", "metadata"):
            held = await store.knowledge.commit_cleanup_cursor(
                owner,
                kb.kb_id,
                held.lease_token,
                held.revision,
                cursor,
                tx,
                document_id=document_id,
            )
    quiet = store.knowledge._quiet

    async def delayed(conn, *args):
        await conn.execute("SELECT pg_sleep(1.1)")
        return await quiet(conn, *args)

    monkeypatch.setattr(store.knowledge, "_quiet", delayed)
    with pytest.raises(AppError, match="final lease"):
        async with store.transaction() as tx:
            await store.knowledge.finish_deletion(
                owner,
                kb.kb_id,
                held.lease_token,
                held.revision,
                tx,
                document_id=document_id,
                index_verified=True,
                objects_verified=True,
            )
    assert (
        await pool.fetchval(
            "SELECT status FROM document_versions WHERE document_version_id=$1",
            values[1].document_version_id,
        )
        == "active"
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id=$1", values[0].document_id
        )
        == 1
    )
    assert (
        await pool.fetchval(
            "SELECT active_version_id FROM documents WHERE document_id=$1", values[0].document_id
        )
        == values[1].document_version_id
    )


async def test_delete_validates_owner_parent_body_key_before_side_effects(
    deleting_http, monkeypatch
):
    from application.errors import AppError

    http, runtime, pool, store, owner = deleting_http
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    other = await store.knowledge.get_kb(owner, UUID((await create(http, "Other"))["kb_id"]))
    values, _, _ = await seed_content(runtime, store, owner, kb)
    before = await pool.fetchval("SELECT count(*) FROM idempotency_requests")
    with pytest.raises(AppError, match="knowledge_base_not_found"):
        await runtime.knowledge_management.delete(uuid4(), kb.kb_id, "wrong-owner")
    response = await http.delete(
        f"/knowledge-bases/{other.kb_id}/documents/{values[0].document_id}",
        headers={"Idempotency-Key": "wrong-parent"},
    )
    assert response.status_code == 404
    endpoint = f"/knowledge-bases/{kb.kb_id}"
    assert (
        await http.request("DELETE", endpoint, json={}, headers={"Idempotency-Key": "body"})
    ).status_code == 422
    assert (await http.delete(endpoint)).status_code == 422
    runtime.knowledge_cleanup.available = False
    assert (
        await http.delete(endpoint, headers={"Idempotency-Key": "no-runner"})
    ).status_code == 503
    assert await pool.fetchval("SELECT count(*) FROM idempotency_requests") == before
    assert (await store.knowledge.get_kb(owner, kb.kb_id)).status == "active"


@pytest.mark.parametrize("kind", ["kb", "document"])
@pytest.mark.parametrize("tamper", ["private_field", "wrong_status", "private_failure"])
async def test_delete_replay_rejects_corrupt_private_cache(deleting_http, kind, tamper):
    import json

    http, runtime, pool, store, owner = deleting_http
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    values, _, _ = await seed_content(runtime, store, owner, kb)
    document_id = values[0].document_id if kind == "document" else None
    endpoint = f"/knowledge-bases/{kb.kb_id}" + (f"/documents/{document_id}" if document_id else "")
    accepted = await http.delete(endpoint, headers={"Idempotency-Key": "cache"})
    assert accepted.status_code == 202
    await deleted(http, endpoint)
    body = accepted.json()
    if tamper == "private_field":
        body["lease_owner"] = "private-canary"
    elif tamper == "wrong_status":
        body["status"] = "deleted"
    else:
        body["failure"] = {"details": {"private": "private-canary"}}
    await pool.execute(
        "UPDATE idempotency_requests SET response_body=$1::jsonb WHERE operation=$2 AND key='cache'",
        json.dumps(body),
        f"knowledge:{kb.kb_id}:delete:{document_id or 'kb'}",
    )
    replay = await http.delete(endpoint, headers={"Idempotency-Key": "cache"})
    assert replay.status_code == 503 and "private-canary" not in replay.text


async def test_tombstone_gc_failure_never_revives_resource_and_retry_clears_failure(
    deleting_http, monkeypatch
):
    from domain.ports import AdapterError

    http, runtime, pool, store, owner = deleting_http
    kb = await store.knowledge.get_kb(owner, UUID((await create(http))["kb_id"]))
    _, keys, _ = await seed_content(runtime, store, owner, kb)
    endpoint = f"/knowledge-bases/{kb.kb_id}"
    assert (
        await http.delete(endpoint, headers={"Idempotency-Key": "gc-failure"})
    ).status_code == 202
    tombstone = await deleted(http, endpoint)
    await runtime.knowledge_content.put(keys[0], b"source", media_type="application/pdf")
    original = runtime.knowledge_content.delete_prefix

    async def unavailable(prefix):
        raise AdapterError("minio", "dependency_unavailable", "private-canary", True, "delete")

    monkeypatch.setattr(runtime.knowledge_content, "delete_prefix", unavailable)
    await pool.execute(
        "UPDATE knowledge_bases SET cleanup_verified_at=clock_timestamp()-interval '2 minutes' WHERE kb_id=$1",
        kb.kb_id,
    )
    await runtime.knowledge_cleanup.tick()
    failed = (await http.get(endpoint)).json()
    assert failed["status"] == "deleted" and failed["revision"] > tombstone["revision"]
    assert failed["failure"]["code"] == "content_unavailable" and "private-canary" not in str(
        failed
    )
    assert await runtime.knowledge_content.head(keys[0])
    monkeypatch.setattr(runtime.knowledge_content, "delete_prefix", original)
    await pool.execute(
        "UPDATE knowledge_bases SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE kb_id=$1",
        kb.kb_id,
    )
    await runtime.knowledge_cleanup.tick()
    recovered = (await http.get(endpoint)).json()
    assert recovered["status"] == "deleted" and recovered["failure"] is None
    assert recovered["revision"] == failed["revision"] + 1
    await missing_objects(runtime.knowledge_content, keys)
