"""Contract test for EmbeddingPort (BGE-M3, mocked model)."""

import pytest

from domain.ports import Embedding
from infrastructure.embedding.bge_m3 import BGEM3Embedding


class _FakeModel:
    def encode(self, text, normalize_embeddings=True):
        class _Arr(list):
            def tolist(self):
                return list(self)

        return _Arr([1.0, 2.0])


@pytest.mark.asyncio
async def test_bge_m3_returns_embedding() -> None:
    adapter = BGEM3Embedding()
    adapter._model = _FakeModel()  # bypass lazy load
    result = await adapter.embed("hello")
    assert isinstance(result, Embedding)
    assert result.dense == [1.0, 2.0]
