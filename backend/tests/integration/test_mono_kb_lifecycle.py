"""Real isolated PG ownership, visibility and atomic activation, no fake index claim."""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from application.errors import AppError
from domain.ports import AdapterError
from infrastructure.storage.research_postgres import encode, insert
from tests.integration.test_mono_document_content import content_store as shared_content_store
from tests.integration.test_mono_transactions import setup_store
from tests.knowledge_fixtures import chunk, content_key, knowledge_base, submission

content_store = shared_content_store


async def seeded(pg_database, *, status="active"):
    pool, store, owner = await setup_store(pg_database)
    kb = knowledge_base(owner.user_id, status=status)
    async with store.transaction() as tx:
        await store.knowledge.create_kb(kb, tx)
    return pool, store, owner.user_id, kb


async def submit(store, owner, values, *, create=True):
    async with store.transaction() as tx:
        return await store.knowledge.submit(owner, *values, tx, create_document=create)


async def activate(store, owner, values):
    _, version, job = values
    async with store.transaction() as tx:
        held = await store.knowledge.claim_job(owner, job.job_id, "fixture-worker", tx)
    async with store.transaction() as tx:
        result = await store.knowledge.activate(
            owner,
            job.job_id,
            held.lease_token,
            [chunk(version)],
            content_key(version, b"parsed"),
            content_key(version, b"manifest"),
            tx,
        )
    return result


async def test_owner_scoped_accept_staging_and_atomic_activation(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    version, job, reused = await submit(store, owner, values)
    assert not reused and version.status == "staging" and job.status == "accepted"
    assert await store.knowledge.visible_chunks(owner, kb.kb_id) == []
    assert await store.knowledge.get_kb(uuid4(), kb.kb_id) is None
    assert await store.knowledge.get_job(uuid4(), job.job_id) is None
    completed = await activate(store, owner, values)
    assert completed.status == "completed" and completed.lease_owner is None
    assert completed.attempt_count == 1 and completed.attempt_history[0].status == "completed"
    assert await store.knowledge.visible_chunks(owner, kb.kb_id) == [chunk(version)]
    assert await store.knowledge.visible_chunks(uuid4(), kb.kb_id) == []
    assert (
        await pool.fetchval("SELECT active_version_id FROM documents")
        == version.document_version_id
    )
    assert await pool.fetchval("SELECT status FROM document_versions") == "active"


async def test_concurrent_duplicate_content_converges_without_extra_document(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    results = await asyncio.gather(*(submit(store, owner, submission(kb)) for _ in range(2)))
    assert results[0][0].document_version_id == results[1][0].document_version_id
    assert results[0][1].job_id == results[1][1].job_id
    assert sorted(result[2] for result in results) == [False, True]
    for table in ("documents", "document_versions", "ingestion_jobs"):
        assert await pool.fetchval(f"SELECT count(*) FROM {table}") == 1
    document = submission(kb)[0]
    with pytest.raises(AppError, match="content_identity_conflict"):
        await submit(store, owner, submission(kb, document), create=False)


async def test_document_has_at_most_one_active_job_and_failure_rolls_back_version(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    initial = submission(kb)
    await submit(store, owner, initial)
    replacement = submission(kb, initial[0], body=b"New source")
    with pytest.raises(AppError, match="document_busy"):
        await submit(store, owner, replacement, create=False)
    assert await pool.fetchval("SELECT count(*) FROM document_versions") == 1
    assert await pool.fetchval("SELECT count(*) FROM ingestion_jobs") == 1


async def test_replacement_retires_version_but_preserves_completed_job_history(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    first = submission(kb)
    await submit(store, owner, first)
    await activate(store, owner, first)
    second = submission(kb, first[0], body=b"New source")
    await submit(store, owner, second, create=False)
    assert await store.knowledge.visible_chunks(owner, kb.kb_id) == [chunk(first[1])]
    await activate(store, owner, second)
    assert await store.knowledge.visible_chunks(owner, kb.kb_id) == [chunk(second[1])]
    assert (
        await pool.fetchval(
            "SELECT status FROM document_versions WHERE document_version_id=$1",
            first[1].document_version_id,
        )
        == "retired"
    )
    assert (await store.knowledge.get_job(owner, first[2].job_id)).status == "completed"
    assert await pool.fetchval("SELECT count(*) FROM chunks") == 2


async def test_failure_during_publish_preserves_old_active_and_no_partial_chunks(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    first = submission(kb)
    await submit(store, owner, first)
    await activate(store, owner, first)
    second = submission(kb, first[0], body=b"New source")
    await submit(store, owner, second, create=False)
    async with store.transaction() as tx:
        held = await store.knowledge.claim_job(owner, second[2].job_id, "fixture", tx)
    await pool.execute("""
        CREATE FUNCTION reject_kb_commit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'test fault' USING ERRCODE='22012'; END $$;
        CREATE TRIGGER reject_kb_commit BEFORE UPDATE ON ingestion_jobs
        FOR EACH ROW EXECUTE FUNCTION reject_kb_commit();
    """)
    with pytest.raises(AdapterError):
        async with store.transaction() as tx:
            await store.knowledge.activate(
                owner,
                held.job_id,
                held.lease_token,
                [chunk(second[1])],
                content_key(second[1], b"parsed"),
                content_key(second[1], b"manifest"),
                tx,
            )
    assert await store.knowledge.visible_chunks(owner, kb.kb_id) == [chunk(first[1])]
    assert await pool.fetchval("SELECT count(*) FROM chunks") == 1
    assert (
        await pool.fetchval(
            "SELECT status FROM document_versions WHERE document_version_id=$1",
            second[1].document_version_id,
        )
        == "staging"
    )
    assert (await store.knowledge.get_job(owner, held.job_id)).status == "processing"


async def test_failed_replacement_keeps_old_active_and_failed_attempt_history(pg_database):
    from datetime import UTC, datetime

    from domain.research.models import Failure

    pool, store, owner, kb = await seeded(pg_database)
    first = submission(kb)
    await submit(store, owner, first)
    await activate(store, owner, first)
    replacement = submission(kb, first[0], body=b"New source")
    await submit(store, owner, replacement, create=False)
    async with store.transaction() as tx:
        held = await store.knowledge.claim_job(owner, replacement[2].job_id, "fixture", tx)
    failure = Failure(
        code="parser_failed",
        dependency="parser",
        operation="parse",
        phase=None,
        message="Controlled parser failure",
        retryable=True,
        resume_allowed=True,
        attempt=1,
        occurred_at=datetime.now(UTC),
        details=None,
    )
    async with store.transaction() as tx:
        failed = await store.knowledge.fail_job(owner, held.job_id, held.lease_token, failure, tx)
    assert failed.status == "failed" and failed.attempt_history[-1].failure == failure
    assert failed.lease_owner is None
    assert await store.knowledge.visible_chunks(owner, kb.kb_id) == [chunk(first[1])]
    assert (
        await pool.fetchval(
            "SELECT status FROM document_versions WHERE document_version_id=$1",
            replacement[1].document_version_id,
        )
        == "failed"
    )


async def test_lease_expiring_during_chunk_commit_rolls_back_activation(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    await submit(store, owner, values)
    await pool.execute("""
        CREATE FUNCTION delay_chunk_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_sleep(0.3); RETURN NEW; END $$;
        CREATE TRIGGER delay_chunk_write BEFORE INSERT ON chunks
        FOR EACH ROW EXECUTE FUNCTION delay_chunk_write();
    """)
    async with store.transaction() as tx:
        held = await store.knowledge.claim_job(owner, values[2].job_id, "fixture", tx, lease_s=1)
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            await store.knowledge.activate(
                owner,
                held.job_id,
                held.lease_token,
                [chunk(values[1], ordinal=i) for i in range(4)],
                content_key(values[1], b"parsed"),
                content_key(values[1], b"manifest"),
                tx,
            )
    assert await pool.fetchval("SELECT count(*) FROM chunks") == 0
    assert await pool.fetchval("SELECT active_version_id FROM documents") is None
    assert await pool.fetchval("SELECT status FROM document_versions") == "staging"


@pytest.mark.parametrize(
    "fence", ["token", "expired", "cancelled", "kb_deleting", "document_deleting"]
)
async def test_activation_refuses_stale_lease_cancel_and_deletion(pg_database, fence):
    pool, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    await submit(store, owner, values)
    async with store.transaction() as tx:
        held = await store.knowledge.claim_job(owner, values[2].job_id, "fixture", tx)
    token = held.lease_token
    if fence == "token":
        token += 1
    elif fence == "expired":
        await pool.execute(
            "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
    elif fence == "cancelled":
        await pool.execute(
            "UPDATE ingestion_jobs SET status='cancelling',cancel_requested_at=clock_timestamp()"
        )
    elif fence == "kb_deleting":
        await pool.execute("UPDATE knowledge_bases SET status='deleting'")
    else:
        await pool.execute("UPDATE documents SET status='deleting'")
    with pytest.raises(AppError, match="stale_resource|resource_not_active"):
        async with store.transaction() as tx:
            await store.knowledge.activate(
                owner,
                held.job_id,
                token,
                [chunk(values[1])],
                content_key(values[1], b"parsed"),
                content_key(values[1], b"manifest"),
                tx,
            )
    assert await pool.fetchval("SELECT count(*) FROM chunks") == 0
    assert await store.knowledge.visible_chunks(owner, kb.kb_id) == []


async def test_database_parent_fks_and_deferred_active_pointer_are_enforced(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    await submit(store, owner, values)
    _, version, _ = values
    wrong = version.model_dump() | {
        "document_version_id": uuid4(),
        "kb_id": uuid4(),
        "content_hash": "f" * 64,
    }
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await insert(conn, "document_versions", wrong)
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE documents SET active_version_id=$1", version.document_version_id
                )
    assert await pool.fetchval("SELECT active_version_id FROM documents") is None
    invalid = chunk(version).model_dump() | {"kb_id": uuid4()}
    with pytest.raises(AppError, match="invalid_state"):
        async with store.transaction() as tx:
            await insert(
                store.connection(tx),
                "chunks",
                encode(type(chunk(version)).model_validate(invalid), {"location", "metadata"}),
            )


@pytest.mark.parametrize("status", ["creating", "deleting", "deleted"])
async def test_inactive_kb_never_accepts_ingestion(pg_database, status):
    pool, store, owner, kb = await seeded(pg_database, status=status)
    with pytest.raises(AppError, match="resource_not_active"):
        await submit(store, owner, submission(kb))
    assert await pool.fetchval("SELECT count(*) FROM documents") == 0


@pytest.mark.parametrize("scope", ["owner", "kb", "version", "hash"])
async def test_source_reference_cannot_cross_scope_or_hash(pg_database, scope):
    pool, store, owner, kb = await seeded(pg_database)
    document, version, job = submission(kb)
    parts = version.source_object_key.split("/")
    parts[{"owner": 1, "kb": 2, "version": 3, "hash": 4}[scope]] = (
        "f" * 64 if scope == "hash" else str(uuid4())
    )
    version = type(version).model_validate(
        version.model_dump() | {"source_object_key": "/".join(parts)}
    )
    with pytest.raises(AppError, match="invalid_state"):
        await submit(store, owner, (document, version, job))
    assert await pool.fetchval("SELECT count(*) FROM documents") == 0


async def test_owner_kb_version_content_namespace_roundtrip_and_precise_cleanup(content_store):
    from hashlib import sha256

    owner, kb, version = uuid4(), uuid4(), uuid4()
    prefixes = [
        f"knowledge-content/{owner}/{kb}/{version}/",
        f"knowledge-content/{owner}/{kb}/{uuid4()}/",
        f"knowledge-content/{owner}/{uuid4()}/{uuid4()}/",
        f"knowledge-content/{uuid4()}/{uuid4()}/{uuid4()}/",
    ]
    body = b"Actual owner-scoped stored bytes"
    keys = [prefix + sha256(body).hexdigest() for prefix in prefixes]
    for key in keys:
        reference = await content_store.put(key, body, media_type="text/plain")
        assert await content_store.read(reference) == body
    with pytest.raises(ValueError):
        await content_store.delete_prefix(f"knowledge-content/{owner}/")
    await content_store.delete_prefix(prefixes[0])
    with pytest.raises(AdapterError, match="content_missing"):
        await content_store.head(keys[0])
    assert await content_store.head(keys[1])
    await content_store.delete_prefix(f"knowledge-content/{owner}/{kb}/")
    with pytest.raises(AdapterError, match="content_missing"):
        await content_store.head(keys[1])
    for key in keys[2:]:
        assert await content_store.read(await content_store.head(key)) == body
