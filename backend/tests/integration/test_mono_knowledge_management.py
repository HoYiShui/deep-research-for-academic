"""Real PG CAS and immediate delete barriers; not physical cleanup acceptance."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from application.errors import AppError
from application.knowledge_models import KnowledgeBasePatch
from tests.integration.test_mono_kb_lifecycle import activate, seeded, submit
from tests.knowledge_fixtures import chunk, knowledge_base, submission


async def update(store, owner, kb, patch):
    async with store.transaction() as tx:
        return await store.knowledge.update_kb(owner, kb.kb_id, patch, tx)


async def deleting(store, owner, kb, document=None):
    async with store.transaction() as tx:
        if document is None:
            return await store.knowledge.mark_kb_deleting(owner, kb.kb_id, tx)
        return await store.knowledge.mark_document_deleting(
            owner, kb.kb_id, document.document_id, tx
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"revision": 1},
        {"revision": True, "name": "new"},
        {"revision": 1, "name": None},
        {"revision": 1, "name": "  "},
        {"revision": 1, "name": "x" * 101},
        {"revision": 1, "description": "x" * 2001},
        {"revision": 1, "data_classification": "public"},
        {"revision": 1, "index_version": "new"},
    ],
)
def test_patch_rejects_empty_invalid_or_immutable_fields(patch):
    with pytest.raises(ValidationError):
        KnowledgeBasePatch.model_validate(patch)


async def test_patch_presence_cas_and_private_classification(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    first = await update(store, owner, kb, {"revision": 1, "description": "keep me"})
    second = await update(store, owner, kb, {"revision": 2, "name": " renamed "})
    assert second.name == "renamed" and second.description == "keep me"
    assert second.data_classification == "private" and second.index_version == kb.index_version
    assert second.revision == 3 and second.updated_at >= first.updated_at
    third = await update(store, owner, kb, {"revision": 3, "description": None})
    assert third.description is None and third.revision == 4
    with pytest.raises(AppError, match="stale_resource"):
        await update(store, owner, kb, {"revision": 3, "name": "stale"})
    assert (await store.knowledge.get_kb(owner, kb.kb_id)).name == "renamed"


async def test_concurrent_patch_only_one_revision_wins(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    results = await asyncio.gather(
        *(update(store, owner, kb, {"revision": 1, "name": name}) for name in ["A", "B"]),
        return_exceptions=True,
    )
    assert (
        sum(isinstance(result, AppError) and result.code == "stale_resource" for result in results)
        == 1
    )
    current = await store.knowledge.get_kb(owner, kb.kb_id)
    assert current.name in {"A", "B"} and current.revision == 2


async def test_duplicate_name_rollback_and_wrong_owner_404(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    other = knowledge_base(owner, name="taken")
    async with store.transaction() as tx:
        await store.knowledge.create_kb(other, tx)
    with pytest.raises(AppError, match="name_already_exists"):
        await update(store, owner, kb, {"revision": 1, "name": "taken"})
    assert (await store.knowledge.get_kb(owner, kb.kb_id)).revision == 1
    for operation in [
        update(store, uuid4(), kb, {"revision": 1, "name": "not mine"}),
        deleting(store, uuid4(), kb),
    ]:
        with pytest.raises(AppError, match="knowledge_base_not_found"):
            await operation
    assert (await store.knowledge.get_kb(owner, kb.kb_id)).status == "active"


@pytest.mark.parametrize("scope", ["kb", "document"])
async def test_delete_barrier_hides_chunks_preserves_history_and_blocks_late_publish(
    pg_database, scope
):
    pool, store, owner, kb = await seeded(pg_database)
    first = submission(kb)
    await submit(store, owner, first)
    await activate(store, owner, first)
    second = submission(kb, first[0], body=b"replacement")
    await submit(store, owner, second, create=False)
    async with store.transaction() as tx:
        held = await store.knowledge.claim_job(owner, second[2].job_id, "worker", tx)
    document = first[0] if scope == "document" else None
    barrier = await deleting(store, owner, kb, document)
    assert barrier.status == "deleting" and barrier.cleanup_cursor == "wait_jobs"
    repeated = await deleting(store, owner, kb, document)
    assert repeated == barrier  # no extra revision or lost cursor
    assert await store.knowledge.visible_chunks(owner, kb.kb_id) == []
    with pytest.raises(AppError, match="resource_not_active"):
        async with store.transaction() as tx:
            await store.knowledge.activate(
                owner,
                second[2].job_id,
                held.lease_token,
                [chunk(second[1])],
                "unused",
                "unused",
                tx,
            )
    with pytest.raises(AppError, match="resource_not_active"):
        await submit(store, owner, submission(kb, first[0], body=b"new"), create=False)
    # Barrier does not erase committed metadata or claim external deletion.
    assert await pool.fetchval("SELECT count(*) FROM chunks") == 1
    assert await pool.fetchval("SELECT count(*) FROM document_versions") == 2
    assert (await store.knowledge.get_job(owner, first[2].job_id)).status == "completed"
    assert (await store.knowledge.get_job(owner, second[2].job_id)).status == "processing"


async def test_creating_delete_and_inactive_patch(pg_database):
    _, store, owner, kb = await seeded(pg_database, status="creating")
    with pytest.raises(AppError, match="resource_not_active"):
        await update(store, owner, kb, {"revision": 1, "description": "bad"})
    barrier = await deleting(store, owner, kb)
    assert barrier.status == "deleting" and barrier.revision == 2
    with pytest.raises(AppError, match="resource_not_active"):
        await update(store, owner, kb, {"revision": 2, "name": "bad"})


async def test_document_delete_validates_parent_and_rolls_back(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    await submit(store, owner, values)
    other = knowledge_base(owner, name="other")
    async with store.transaction() as tx:
        await store.knowledge.create_kb(other, tx)
    with pytest.raises(AppError, match="document_not_found"):
        await deleting(store, owner, other, values[0])
    with pytest.raises(AppError, match="knowledge_base_not_found"):
        await deleting(store, uuid4(), kb, values[0])
    with pytest.raises(RuntimeError):
        async with store.transaction() as tx:
            await store.knowledge.mark_document_deleting(owner, kb.kb_id, values[0].document_id, tx)
            raise RuntimeError("force rollback")
    async with store.transaction() as tx:
        context = await store.knowledge.job_context(owner, values[2].job_id, tx)
    assert context.document.status == "active" and context.document.revision == 1


async def test_patch_races_delete_without_resurrecting_or_resetting_cursor(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    results = await asyncio.gather(
        update(store, owner, kb, {"revision": 1, "name": "raced"}),
        deleting(store, owner, kb),
        return_exceptions=True,
    )
    assert not isinstance(results[1], Exception)
    if isinstance(results[0], Exception):
        assert isinstance(results[0], AppError) and results[0].code == "resource_not_active"
    current = await store.knowledge.get_kb(owner, kb.kb_id)
    assert current.status == "deleting" and current.cleanup_cursor == "wait_jobs"
    assert current.revision == (2 if isinstance(results[0], Exception) else 3)


async def test_deleted_tombstone_repeat_is_read_only(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    # Explicit metadata fixture, not a claim that physical cleanup ran.
    await pool.execute(
        "UPDATE knowledge_bases SET status='deleted',revision=2 WHERE kb_id=$1", kb.kb_id
    )
    tombstone = await store.knowledge.get_kb(owner, kb.kb_id)
    assert await deleting(store, owner, kb) == tombstone
    async with store.transaction() as tx:
        await store.knowledge.create_kb(knowledge_base(owner, name=kb.name), tx)
    assert await pool.fetchval("SELECT count(*) FROM knowledge_bases") == 2
