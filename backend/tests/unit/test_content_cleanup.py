"""Native cancellation must stop subsequent destructive calls and verify absence."""

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from domain.ports import AdapterError
from infrastructure.storage.content import MinioContentStore


@pytest.mark.asyncio
async def test_prefix_cancel_stops_next_native_delete_and_keeps_slot_until_finish():
    content = MinioContentStore("localhost:9000", "access", "secret", "bucket")
    prefix = f"knowledge-content/{uuid4()}/{uuid4()}/"
    gate, started = threading.Event(), threading.Event()
    removed = []

    def remove(bucket, key):
        removed.append(key)
        started.set()
        assert gate.wait(3)

    content._client = SimpleNamespace(
        list_objects=lambda *args, **kwargs: iter(
            [SimpleNamespace(object_name=prefix + str(i)) for i in range(3)]
        ),
        remove_object=remove,
    )
    task = asyncio.create_task(content.delete_prefix(prefix))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert content._slots._value == 1
        gate.set()
        async with asyncio.timeout(2):
            while content._inflight:
                await asyncio.sleep(0.01)
        assert removed == [prefix + "0"]
        assert content._slots._value == 2
    finally:
        gate.set()
        await content.close()


@pytest.mark.asyncio
async def test_prefix_delete_requires_absence_and_refuses_escaped_listing():
    content = MinioContentStore("localhost:9000", "access", "secret", "bucket")
    prefix = f"knowledge-content/{uuid4()}/{uuid4()}/"
    removed = []
    content._client = SimpleNamespace(
        list_objects=lambda *args, **kwargs: iter(
            [SimpleNamespace(object_name=prefix + "still-present")]
        ),
        remove_object=lambda bucket, key: removed.append(key),
    )
    try:
        with pytest.raises(AdapterError, match="not verified"):
            await content.delete_prefix(prefix)
        assert removed == [prefix + "still-present"]
        content._client.list_objects = lambda *args, **kwargs: iter(
            [SimpleNamespace(object_name="outside/scope")]
        )
        with pytest.raises(AdapterError):
            await content.delete_prefix(prefix)
        assert removed == [prefix + "still-present"]
    finally:
        await content.close()
