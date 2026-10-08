import asyncio
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from application.vector_models import IndexEmbedding, VectorScope
from domain.ports import AdapterError
from infrastructure.vector.index import MilvusIndex, expression, partition


def test_fixed_index_exposes_the_typed_application_contract():
    from typing import get_type_hints

    from application.ports import VectorIndexPort

    for method in (
        "ensure_schema",
        "ensure_partition",
        "upsert",
        "verify_rows",
        "hybrid_search",
        "delete_version",
        "drop_partition",
        "close",
    ):
        assert callable(getattr(VectorIndexPort, method))
        assert (
            get_type_hints(getattr(MilvusIndex, method))["return"]
            == get_type_hints(getattr(VectorIndexPort, method))["return"]
        )


@pytest.mark.parametrize(
    "dense,sparse",
    [
        ([1.0], {1: 1.0}),
        ([0.0] * 1024, {1: 1.0}),
        ([float("nan")] * 1024, {1: 1.0}),
        ([1.0] * 1024, {}),
        ([1.0] * 1024, {True: 1.0}),
        ([1.0] * 1024, {-1: 1.0}),
        ([1.0] * 1024, {1: float("inf")}),
        ([1.0] * 1024, {1: 0.0}),
    ],
)
def test_vector_shapes_reject_invalid_values(dense, sparse):
    with pytest.raises(ValidationError):
        IndexEmbedding(dense_vector=dense, sparse_vector=sparse)


def test_scope_requires_allowlist_and_not_arbitrary_expression():
    kb, version = uuid4(), uuid4()
    for extra in [
        {"version_ids": []},
        {"filter": "true"},
        {"version_ids": [version, version]},
        {"year_min": 2024, "year_max": 2020},
    ]:
        with pytest.raises(ValidationError):
            VectorScope.model_validate(
                {"kb_id": kb, "version_ids": [version], "index_version": "v1"} | extra
            )
    scope = VectorScope(kb_id=kb, version_ids=[version], index_version='v1" or true')
    assert '\\" or true' in expression(scope)
    assert partition(kb) == "kb_" + kb.hex
    with pytest.raises(TypeError):
        partition("default")


@pytest.mark.parametrize(
    "uri", ["milvus.db", "file:///tmp/milvus.db", "http://localhost:19530/collection"]
)
def test_index_rejects_lite_or_path_endpoint(uri):
    with pytest.raises(ValueError):
        MilvusIndex(uri)


async def test_cancelled_caller_does_not_release_running_native_operation_slot():
    adapter = MilvusIndex("http://127.0.0.1:1")
    entered, release, next_entered = threading.Event(), threading.Event(), threading.Event()

    def native():
        entered.set()
        if not release.wait(3):
            raise RuntimeError("Unit test native deadline")

    first = asyncio.create_task(adapter._call("controlled_native", native))
    later = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        later = asyncio.create_task(adapter._call("next", next_entered.set))
        await asyncio.sleep(0.03)
        assert not next_entered.is_set()
        release.set()
        await later
        assert next_entered.is_set()
    finally:
        release.set()
        if later is not None:
            await later
        await adapter.close()


async def test_adapter_error_is_sanitized_and_close_is_idempotent():
    adapter = MilvusIndex("http://127.0.0.1:1")

    def broken():
        raise RuntimeError("private-sdk-canary")

    try:
        with pytest.raises(AdapterError) as error:
            await adapter._call("controlled_native", broken)
        assert "private-sdk-canary" not in str(error.value)
    finally:
        await adapter.close()
        await adapter.close()
    with pytest.raises(AdapterError):
        await adapter._call("closed", lambda: True)


async def test_cancelled_native_sequence_cannot_start_its_next_sdk_step():
    adapter = MilvusIndex("http://127.0.0.1:1")
    entered, release, next_io = threading.Event(), threading.Event(), threading.Event()

    def sequence():
        entered.set()
        if not release.wait(3):
            raise RuntimeError("Unit native deadline")
        _ = adapter.timeout  # real SDK calls obtain their remaining timeout here
        next_io.set()

    task = asyncio.create_task(adapter._call("controlled_sequence", sequence))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
    finally:
        release.set()
        await adapter.close()
    assert not next_io.is_set()


async def test_total_operation_deadline_stops_caller_and_future_sdk_steps():
    adapter = MilvusIndex("http://127.0.0.1:1", timeout_s=1)
    release, next_io = threading.Event(), threading.Event()

    def sequence():
        if not release.wait(3):
            raise RuntimeError("Unit native deadline")
        _ = adapter.timeout
        next_io.set()

    try:
        with pytest.raises(AdapterError):
            await adapter._call("controlled_sequence", sequence)
    finally:
        release.set()
        await adapter.close()
    assert not next_io.is_set()
