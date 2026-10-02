"""Contract test for VectorStorePort (Milvus, mocked client)."""

import pytest

from domain.ports import Embedding
from infrastructure.vector.milvus import MilvusStore


class _FakeClient:
    def search(self, collection_name, data, limit, output_fields):
        return [[{"id": "c1", "distance": 0.9, "entity": {"text": "x"}}]]

    def insert(self, collection_name, data):
        pass


@pytest.mark.asyncio
async def test_milvus_hybrid_search_returns_chunks() -> None:
    store = MilvusStore()
    store._client = _FakeClient()
    chunks = await store.hybrid_search("kb", Embedding([1.0], {}), 5)
    assert chunks[0].chunk_id == "c1"
    assert chunks[0].text == "x"
