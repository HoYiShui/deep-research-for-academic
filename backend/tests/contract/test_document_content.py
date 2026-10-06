"""Shared ContentStore invariants; no network in these boundary tests."""

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from domain.ports import AdapterError
from infrastructure.storage.content import MinioContentStore, content_key, content_prefix


def key(body=b"real original", *, run=None):
    return f"research-content/{run or uuid4()}/{hashlib.sha256(body).hexdigest()}"


@pytest.fixture
def store():
    value = MinioContentStore("localhost:9000", "test-access", "test-secret", "test-bucket")
    value._client = Mock()
    return value


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../x",
        "documents",
        "documents/run1/" + "a" * 64,
        "research-content/run1/" + "a" * 64,
        "research-content/" + str(uuid4()) + "/../" + "a" * 64,
        "documents/" + str(uuid4()) + "/" + str(uuid4()) + "/original.pdf",
    ],
)
def test_key_scope_and_hash_required(path):
    with pytest.raises(ValueError):
        content_key(path)


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "/",
        "documents/",
        "research-content/",
        "../",
        "documents/*/",
        "documents/run1/",
        "research-content/" + str(uuid4()),
    ],
)
def test_no_broad_deletion_prefix(prefix):
    with pytest.raises(ValueError):
        content_prefix(prefix)


@pytest.mark.asyncio
async def test_same_key_different_bytes_or_expected_hash_rejected_before_sdk(store):
    try:
        for body, expected in ((b"wrong", None), (b"real original", "0" * 64)):
            with pytest.raises(AdapterError) as caught:
                await store.put(
                    key(b"real original"), body, media_type="text/plain", expected_hash=expected
                )
            assert caught.value.code == "content_hash_mismatch"
        store._client.get_object.assert_not_called()
        store._client.put_object.assert_not_called()
        store._client.stat_object.assert_not_called()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stream_limit_and_chunk_type_before_sdk(store):
    store.max_bytes = 4  # test limit seam, production hard limit is 50MiB

    async def oversized():
        yield b"123"
        yield b"45"

    async def invalid():
        yield "not bytes"

    try:
        with pytest.raises(ValueError, match="50MiB"):
            await store.put(key(b"12345"), oversized(), media_type="text/plain")
        with pytest.raises(TypeError):
            await store.put(key(), invalid(), media_type="text/plain")
        store._client.put_object.assert_not_called()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_stream_never_starts_sdk_write(store):
    started, exited = asyncio.Event(), asyncio.Event()

    async def stream():
        try:
            started.set()
            await asyncio.Event().wait()
            yield b"bytes"
        finally:
            exited.set()

    job = asyncio.create_task(store.put(key(), stream(), media_type="text/plain"))
    try:
        await started.wait()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert exited.is_set()
        store._client.put_object.assert_not_called()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_verifies_complete_bytes_before_yield(store):
    body = b"real original"
    store._client.stat_object.return_value = SimpleNamespace(
        size=len(body), content_type="text/plain"
    )
    response = Mock()
    response.read.return_value = body
    store._client.get_object.return_value = response
    try:
        items = [chunk async for chunk in await store.get(key(body))]
        assert b"".join(items) == body
        response.close.assert_called_once()
        response.release_conn.assert_called_once()
        response.read.return_value = b"tampered"
        with pytest.raises(AdapterError, match="content_hash_mismatch"):
            await store.get(key(body))
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_head_rejects_invalid_metadata_and_missing_is_not_empty(store):
    store._client.stat_object.return_value = SimpleNamespace(
        size=store.max_bytes + 1, content_type="text/plain"
    )
    try:
        with pytest.raises(AdapterError, match="content_hash_mismatch"):
            await store.head(key())
        store._client.stat_object.side_effect = RuntimeError("SECRET")
        with pytest.raises(AdapterError) as caught:
            await store.head(key())
        assert caught.value.operation == "content_store"
        assert "SECRET" not in str(caught.value)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_guards_before_sdk(store):
    try:
        with pytest.raises(ValueError):
            await store.delete_prefix("documents/")
        with pytest.raises(ValueError):
            await store.delete("../user-file")
        store._client.list_objects.assert_not_called()
        store._client.remove_object.assert_not_called()
    finally:
        await store.close()
