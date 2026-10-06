"""Real MinIO I/O in an exact, invocation-owned disposable bucket."""

import asyncio

import pytest

from application.settings import Settings
from domain.ports import AdapterError
from infrastructure.storage.content_cache import MinioResultCache


@pytest.mark.asyncio
async def test_real_cache_roundtrip_reopen_and_concurrent_dedup(object_cache):
    cache = object_cache
    data = b'{"result":[],"usage":{"input_tokens":17,"output_tokens":4}}'
    references = await asyncio.gather(
        *(cache.put("tool-results/run1", data, "application/json") for _ in range(6))
    )
    assert all(ref == references[0] for ref in references)
    settings = Settings.load()
    reopened = MinioResultCache(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        cache.bucket,
        secure=settings.minio_secure,
    )
    try:
        assert await reopened.read(references[0]) == data
        items = await asyncio.to_thread(lambda: list(cache._client.list_objects(cache.bucket)))
        assert len(items) == 1
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_real_cache_empty_missing_and_corruption(object_cache):
    import io

    cache = object_cache
    reference = await cache.put("tool-results/run1", b"", "application/json")
    assert await cache.read(reference) == b""
    await asyncio.to_thread(cache._client.remove_object, cache.bucket, reference.key)
    with pytest.raises(AdapterError) as missing:
        await cache.read(reference)
    assert missing.value.code == "content_missing"
    await asyncio.to_thread(
        cache._client.put_object, cache.bucket, reference.key, io.BytesIO(b"corruption"), 10
    )
    with pytest.raises(AdapterError) as corrupt:
        await cache.read(reference)
    assert corrupt.value.code == "content_hash_mismatch"
    with pytest.raises(AdapterError) as no_overwrite:
        await cache.put("tool-results/run1", b"", "application/json")
    assert no_overwrite.value.code == "content_hash_mismatch"
