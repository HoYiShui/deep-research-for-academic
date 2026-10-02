"""Contract test for RerankPort (BGE-reranker, mocked model)."""

import pytest

from domain.ports import Chunk
from infrastructure.embedding.bge_reranker import BGEReranker


class _FakeModel:
    def predict(self, pairs):
        return [0.9, 0.5]


@pytest.mark.asyncio
async def test_reranker_returns_top_k() -> None:
    adapter = BGEReranker()
    adapter._model = _FakeModel()
    candidates = [Chunk("c1", "a", 0.0, {}), Chunk("c2", "b", 0.0, {})]
    result = await adapter.rerank("q", candidates, 1)
    assert len(result) == 1
    assert result[0].chunk_id == "c1"
