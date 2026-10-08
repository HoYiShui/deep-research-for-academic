"""Real Standalone with owned partitions and controlled vectors, not BGE acceptance."""

import os
import socket
from uuid import uuid4

import pytest
import pytest_asyncio

from application.settings import Settings
from application.vector_models import IndexEmbedding, IndexRow, VectorScope
from domain.ports import AdapterError
from infrastructure.vector.index import MilvusIndex
from tests.integration.test_mono_kb_lifecycle import activate, seeded, submit
from tests.knowledge_fixtures import chunk, submission


def vector(axis=0):
    return [1.0 if position == axis else 0.0 for position in range(1024)]


def row(kb, version, identity, *, axis=0, token=7, year=None):
    return IndexRow(
        chunk_id=identity,
        kb_id=kb,
        document_id=uuid4(),
        document_version_id=version,
        index_version="test-index-v1",
        chunk_type="text",
        page_start=None,
        year=year,
        dense_vector=vector(axis),
        sparse_vector={token: 1.0},
    )


@pytest_asyncio.fixture
async def index():
    # Share the configured Compose service, never another throwaway Docker stack.
    # Only UUID partitions created by this fixture are removed; the collection remains.
    uri = os.environ.get("DR4A_TEST_MILVUS_URI") or Settings.load().milvus_uri
    adapter = MilvusIndex(uri)
    kb = uuid4()
    try:
        await adapter.ensure_schema("test-index-v1")
        await adapter.ensure_partition(kb)
        yield adapter, kb
    finally:
        try:
            await adapter.drop_partition(kb)
        finally:
            await adapter.close()


async def test_real_schema_partition_upsert_and_strong_verification(index):
    adapter, kb = index
    values = [row(kb, uuid4(), uuid4().hex), row(kb, uuid4(), uuid4().hex, year=2024)]
    await adapter.ensure_schema("test-index-v1")
    await adapter.ensure_partition(kb)
    await adapter.upsert(values)
    assert await adapter.verify_rows(values) is True
    await adapter.upsert(values)
    assert await adapter.verify_rows(values) is True


async def test_real_dual_recall_rrf_and_explicit_version_allowlist(index):
    adapter, kb = index
    active, staging = uuid4(), uuid4()
    dense = row(kb, active, "dense-" + uuid4().hex, axis=0, token=8)
    sparse = row(kb, active, "sparse-" + uuid4().hex, axis=1, token=7)
    hidden = row(kb, staging, "staging-" + uuid4().hex)
    await adapter.upsert([dense, sparse, hidden])
    scope = VectorScope(kb_id=kb, version_ids=[active], index_version="test-index-v1")
    hits = await adapter.hybrid_search(
        IndexEmbedding(dense_vector=vector(), sparse_vector={7: 1.0}), scope
    )
    assert {hit.chunk_id for hit in hits} == {dense.chunk_id, sparse.chunk_id}
    by_id = {hit.chunk_id: hit for hit in hits}
    assert by_id[dense.chunk_id].dense_rank == 1 and by_id[dense.chunk_id].sparse_rank is None
    assert by_id[sparse.chunk_id].dense_rank == 2 and by_id[sparse.chunk_id].sparse_rank == 1
    assert by_id[sparse.chunk_id].fused_score == pytest.approx(1 / 62 + 1 / 61)
    assert hits[0].chunk_id == sparse.chunk_id
    # New version is not public until the PG-derived allowlist changes.
    changed = scope.model_copy(update={"version_ids": [staging]})
    assert [
        hit.chunk_id
        for hit in await adapter.hybrid_search(
            IndexEmbedding(dense_vector=vector(), sparse_vector={7: 1.0}), changed
        )
    ] == [hidden.chunk_id]


async def test_delete_version_does_not_delete_replacement_or_other_kb(index):
    adapter, kb = index
    old, new = row(kb, uuid4(), uuid4().hex), row(kb, uuid4(), uuid4().hex)
    other_kb = uuid4()
    other = row(other_kb, uuid4(), uuid4().hex)
    await adapter.ensure_partition(other_kb)
    try:
        await adapter.upsert([old, new])
        await adapter.upsert([other])
        await adapter.delete_version(kb, old.document_version_id)
        await adapter.delete_version(kb, old.document_version_id)
        with pytest.raises(AdapterError, match="Index"):
            await adapter.verify_rows([old])
        assert await adapter.verify_rows([new]) and await adapter.verify_rows([other])
        await adapter.drop_partition(kb)
        await adapter.drop_partition(kb)
        # Resume/repeated cleanup is also successful after the KB partition is gone.
        await adapter.delete_version(kb, new.document_version_id)
        assert await adapter.verify_rows([other])
    finally:
        await adapter.drop_partition(other_kb)


async def test_explicit_document_type_and_year_filters(index):
    adapter, kb = index
    version = uuid4()
    values = [row(kb, version, uuid4().hex, year=2020), row(kb, version, uuid4().hex, year=2024)]
    await adapter.upsert(values)
    embedding = IndexEmbedding(dense_vector=vector(), sparse_vector={7: 1.0})
    scope = VectorScope(
        kb_id=kb, version_ids=[version], index_version="test-index-v1", year_min=2023
    )
    assert [hit.chunk_id for hit in await adapter.hybrid_search(embedding, scope)] == [
        values[1].chunk_id
    ]
    scope = scope.model_copy(update={"document_ids": [values[0].document_id]})
    assert await adapter.hybrid_search(embedding, scope) == []


async def test_tampered_mapping_or_vectors_cannot_pass_verification(index):
    adapter, kb = index
    expected = row(kb, uuid4(), uuid4().hex)
    await adapter.upsert([expected.model_copy(update={"dense_vector": vector(1)})])
    with pytest.raises(AdapterError):
        await adapter.verify_rows([expected])


async def test_real_pg_visibility_version_switch_drives_index_allowlist(index, pg_database):
    adapter, _ = index
    _, store, owner, kb = await seeded(pg_database)
    await adapter.ensure_partition(kb.kb_id)
    try:
        first = submission(kb)
        await submit(store, owner, first)
        second = submission(kb, first[0], body=b"replacement index fixture")

        def indexed(values):
            document, version, _ = values
            return row(kb.kb_id, version.document_version_id, chunk(version).chunk_id).model_copy(
                update={
                    "document_id": document.document_id,
                    "index_version": kb.index_version,
                    "page_start": chunk(version).location.page_start,
                }
            )

        original = indexed(first)
        await adapter.upsert([original])
        assert await adapter.verify_rows([original])
        await activate(store, owner, first)
        await submit(store, owner, second, create=False)
        replacement = indexed(second)
        await adapter.upsert([replacement])
        assert await adapter.verify_rows([replacement])
        embedding = IndexEmbedding(dense_vector=vector(), sparse_vector={7: 1.0})

        async def visible_hits():
            visible = await store.knowledge.visible_chunks(owner, kb.kb_id)
            scope = VectorScope(
                kb_id=kb.kb_id,
                version_ids=sorted({item.document_version_id for item in visible}),
                index_version=kb.index_version,
            )
            return await adapter.hybrid_search(embedding, scope)

        assert [hit.chunk_id for hit in await visible_hits()] == [original.chunk_id]
        assert await store.knowledge.visible_chunks(uuid4(), kb.kb_id) == []
        await activate(store, owner, second)
        assert [hit.chunk_id for hit in await visible_hits()] == [replacement.chunk_id]
        # Old version remains physical but no longer in the current PG allowlist.
        assert await adapter.verify_rows([original])
    finally:
        await adapter.drop_partition(kb.kb_id)


async def test_actual_unreachable_service_is_error_not_empty_results():
    # Reserve an actual non-listening local port; no fake Milvus response.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        adapter = MilvusIndex(f"http://127.0.0.1:{reserved.getsockname()[1]}", timeout_s=1)
        try:
            with pytest.raises(AdapterError) as error:
                await adapter.ensure_schema("test-index-v1")
            assert error.value.code == "index_not_ready"
        finally:
            await adapter.close()


async def test_pg_publish_rollback_leaves_new_vectors_staging_and_old_version_visible(
    index, pg_database
):
    adapter, _ = index
    _, store, owner, kb = await seeded(pg_database)
    await adapter.ensure_partition(kb.kb_id)
    first = submission(kb)
    second = submission(kb, first[0], body=b"replacement with publish fault")

    def indexed(values):
        document, version, _ = values
        return row(kb.kb_id, version.document_version_id, chunk(version).chunk_id).model_copy(
            update={"document_id": document.document_id, "index_version": kb.index_version}
        )

    try:
        await submit(store, owner, first)
        await adapter.upsert([indexed(first)])
        await activate(store, owner, first)
        await submit(store, owner, second, create=False)
        await adapter.upsert([indexed(second)])
        assert await adapter.verify_rows([indexed(second)])
        async with store.transaction() as tx:
            held = await store.knowledge.claim_job(owner, second[2].job_id, "fault-worker", tx)
        with pytest.raises(RuntimeError, match="publish rollback"):
            async with store.transaction() as tx:
                await store.knowledge.activate(
                    owner,
                    held.job_id,
                    held.lease_token,
                    [chunk(second[1])],
                    second[1].source_object_key,
                    second[1].source_object_key,
                    tx,
                )
                raise RuntimeError("publish rollback")
        visible = await store.knowledge.visible_chunks(owner, kb.kb_id)
        assert visible == [chunk(first[1])]
        scope = VectorScope(
            kb_id=kb.kb_id,
            version_ids=[item.document_version_id for item in visible],
            index_version=kb.index_version,
        )
        hits = await adapter.hybrid_search(
            IndexEmbedding(dense_vector=vector(), sparse_vector={7: 1.0}), scope
        )
        assert [hit.chunk_id for hit in hits] == [chunk(first[1]).chunk_id]
        assert (await store.knowledge.get_job(owner, held.job_id)).status == "processing"
        # PG rollback never makes the physically readable new vector public.
        assert await adapter.verify_rows([indexed(second)])
    finally:
        await adapter.drop_partition(kb.kb_id)


async def test_null_metadata_and_all_structural_types_roundtrip(index):
    adapter, kb = index
    version = uuid4()
    values = [
        row(kb, version, uuid4().hex).model_copy(update={"chunk_type": kind})
        for kind in ("text", "table", "formula")
    ]
    await adapter.upsert(values)
    assert await adapter.verify_rows(values)
    embedding = IndexEmbedding(dense_vector=vector(), sparse_vector={7: 1.0})
    scope = VectorScope(
        kb_id=kb,
        version_ids=[version],
        index_version="test-index-v1",
        chunk_types=["table", "formula"],
    )
    assert {hit.chunk_id for hit in await adapter.hybrid_search(embedding, scope)} == {
        values[1].chunk_id,
        values[2].chunk_id,
    }
    # Unknown years do not satisfy known-year filters.
    assert await adapter.hybrid_search(embedding, scope.model_copy(update={"year_min": 2020})) == []
    assert (
        await adapter.hybrid_search(
            embedding, scope.model_copy(update={"index_version": "another-index-v1"})
        )
        == []
    )


async def test_identical_vectors_in_another_partition_never_leak(index):
    adapter, kb = index
    version, other_kb = uuid4(), uuid4()
    own = row(kb, version, uuid4().hex)
    foreign = row(other_kb, version, uuid4().hex)
    await adapter.ensure_partition(other_kb)
    try:
        await adapter.upsert([own])
        await adapter.upsert([foreign])
        scope = VectorScope(kb_id=kb, version_ids=[version], index_version="test-index-v1")
        hits = await adapter.hybrid_search(
            IndexEmbedding(dense_vector=vector(), sparse_vector={7: 1.0}), scope
        )
        assert [hit.chunk_id for hit in hits] == [own.chunk_id]
        # Deleting this version is confined to the same partition used for writes/reads.
        await adapter.delete_version(kb, version)
        assert await adapter.verify_rows([foreign])
        assert (
            await adapter.hybrid_search(
                IndexEmbedding(dense_vector=vector(), sparse_vector={7: 1.0}), scope
            )
            == []
        )
    finally:
        await adapter.drop_partition(other_kb)
