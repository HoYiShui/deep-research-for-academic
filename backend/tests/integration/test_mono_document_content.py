"""Real MinIO; only exact invocation-owned fixture buckets are mutated."""

import asyncio
import hashlib
import io
from uuid import uuid4

import pytest
import pytest_asyncio

from application.settings import Settings
from domain.ports import AdapterError
from infrastructure.storage.content import MinioContentStore


@pytest_asyncio.fixture
async def content_store(object_cache):
    settings = Settings.load()
    store = MinioContentStore(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        object_cache.bucket,
        secure=settings.minio_secure,
    )
    try:
        yield store
    finally:
        await store.close()


def scoped_key(body, *, kb=None, version=None):
    return f"documents/{kb or uuid4()}/{version or uuid4()}/{hashlib.sha256(body).hexdigest()}"


@pytest.mark.asyncio
async def test_streamed_roundtrip_concurrent_dedup_reopen_and_overwrite_rejection(content_store):
    store = content_store
    body = b"Actual UTF-8 bytes, not a fake body.\n" * 100
    key = scoped_key(body)

    async def stream():
        for offset in range(0, len(body), 17):
            yield body[offset : offset + 17]

    references = await asyncio.gather(
        *(
            store.put(
                key,
                stream(),
                media_type="text/plain",
                expected_hash=hashlib.sha256(body).hexdigest(),
            )
            for _ in range(4)
        )
    )
    assert all(reference == references[0] for reference in references)
    assert b"".join([chunk async for chunk in await store.get(key)]) == body
    assert await store.head(key) == references[0]
    assert await store.read(references[0]) == body
    settings = Settings.load()
    reopened = MinioContentStore(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        store.bucket,
        secure=settings.minio_secure,
    )
    try:
        assert await reopened.read(references[0]) == body
    finally:
        await reopened.close()
    with pytest.raises(AdapterError, match="content_hash_mismatch"):
        await store.put(key, b"different bytes", media_type="text/plain")
    assert await store.read(references[0]) == body
    assert (
        len(
            await asyncio.to_thread(
                lambda: list(store._client.list_objects(store.bucket, recursive=True))
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_corrupt_existing_object_not_repaired_or_overwritten(content_store):
    store = content_store
    body = b"original"
    key = scoped_key(body)
    reference = await store.put(key, body, media_type="text/plain")
    await asyncio.to_thread(store._client.put_object, store.bucket, key, io.BytesIO(b"tampered"), 8)
    with pytest.raises(AdapterError, match="content_hash_mismatch"):
        await store.read(reference)
    with pytest.raises(AdapterError, match="content_hash_mismatch"):
        await store.put(key, body, media_type="text/plain")


@pytest.mark.asyncio
async def test_exact_version_deletion_does_not_touch_other_version_or_kb(content_store):
    store = content_store
    kb, v1, v2 = uuid4(), uuid4(), uuid4()
    keys = [
        scoped_key(b"a", kb=kb, version=v1),
        scoped_key(b"b", kb=kb, version=v2),
        scoped_key(b"c"),
    ]
    for key, body in zip(keys, (b"a", b"b", b"c"), strict=True):
        await store.put(key, body, media_type="text/plain")
    await store.delete_prefix(f"documents/{kb}/{v1}/")
    await store.delete_prefix(f"documents/{kb}/{v1}/")  # idempotent retry
    with pytest.raises(AdapterError, match="content_missing"):
        await store.head(keys[0])
    assert b"".join([part async for part in await store.get(keys[1])]) == b"b"
    assert b"".join([part async for part in await store.get(keys[2])]) == b"c"
    await store.delete(keys[1])
    await store.delete(keys[1])
    with pytest.raises(AdapterError, match="content_missing"):
        await store.get(keys[1])


@pytest.mark.asyncio
async def test_document_store_accepts_original_larger_than_tool_cache(content_store):
    body = b"a" * (11 * 1024 * 1024)
    reference = await content_store.put(scoped_key(body), body, media_type="text/plain")
    assert await content_store.read(reference) == body
