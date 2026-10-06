import asyncio
import hashlib
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from domain.content import ContentRef
from domain.ports import AdapterError
from infrastructure.storage.content_cache import MAX_RESULT_BYTES, MinioResultCache


@pytest.fixture
def cache():
    return MinioResultCache("localhost:9000", "test-access", "test-secret", "test-bucket")


def reference(content=b"hello"):
    digest = hashlib.sha256(content).hexdigest()
    return ContentRef(
        key=f"tool-results/run1/{digest}",
        sha256=digest,
        size=len(content),
        media_type="application/json",
    )


@pytest.mark.asyncio
async def test_integrity_and_connection_release(cache):
    response = Mock()
    response.read.return_value = b"hello"
    cache._client = SimpleNamespace(get_object=Mock(return_value=response))
    try:
        assert await cache.read(reference()) == b"hello"
        response.read.assert_called_once_with(MAX_RESULT_BYTES + 1)
        response.close.assert_called_once()
        response.release_conn.assert_called_once()
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_corruption_never_returns_empty_success(cache):
    response = Mock()
    response.read.return_value = b"wrong"
    cache._client = SimpleNamespace(get_object=Mock(return_value=response))
    try:
        with pytest.raises(AdapterError) as failure:
            await cache.read(reference())
        assert failure.value.code == "content_hash_mismatch"
        response.release_conn.assert_called_once()
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_upstream_exception_is_sanitized(cache):
    cache._client = SimpleNamespace(get_object=Mock(side_effect=RuntimeError("SECRET")))
    try:
        with pytest.raises(AdapterError) as failure:
            await cache.read(reference())
        assert "SECRET" not in str(failure.value)
        assert failure.value.retryable
    finally:
        await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["", "../other", "one", "one/two/three", "one/.."])
async def test_rejects_unscoped_object_names(cache, namespace):
    try:
        with pytest.raises(ValueError):
            await cache.put(namespace, b"x", "text/plain")
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_rejects_oversized_and_mismatched_reference_before_io(cache):
    cache._client = Mock()
    try:
        with pytest.raises(ValueError):
            await cache.put("tool-results/run1", b"x" * (MAX_RESULT_BYTES + 1), "text/plain")
        with pytest.raises(ValueError):
            await cache.read(
                reference().model_copy(update={"key": "tool-results/run1/" + "0" * 64})
            )
        cache._client.get_object.assert_not_called()
        cache._client.put_object.assert_not_called()
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_valid_empty_result_is_distinct_from_failure(cache):
    response = Mock()
    response.read.return_value = b""
    cache._client = SimpleNamespace(get_object=Mock(return_value=response))
    try:
        assert await cache.read(reference(b"")) == b""
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_cancelled_call_keeps_io_slot_until_real_request_finishes(cache):
    gate = threading.Event()
    two_started = threading.Event()
    lock = threading.Lock()
    started = 0

    def get_object(*args):
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                two_started.set()
        if not gate.wait(timeout=5):
            raise TimeoutError("Test gate timed out")
        response = Mock()
        response.read.return_value = b"hello"
        return response

    cache._client = SimpleNamespace(get_object=get_object)
    tasks = [asyncio.create_task(cache.read(reference())) for _ in range(3)]
    try:
        assert await asyncio.to_thread(two_started.wait, 2)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert started == 2
        assert len(cache._inflight) == 2
        assert not tasks[2].done()
        gate.set()
        assert await tasks[1] == b"hello"
        assert await tasks[2] == b"hello"
        assert started == 3
    finally:
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await cache.close()
