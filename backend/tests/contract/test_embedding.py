"""Contract test for EmbeddingPort (BGE-M3, mocked model)."""

import pytest

from domain.ports import Embedding
from infrastructure.embedding.bge_m3 import BGEM3Embedding


class _FakeModel:
    def encode(self, texts, return_dense=True, return_sparse=True):
        class _Vec:
            def tolist(self):
                return [1.0, 2.0]

        return {"dense_vecs": [_Vec()], "lexical_weights": [{0: 0.5, 1: 0.3}]}


@pytest.mark.asyncio
async def test_bge_m3_returns_dual_vector() -> None:
    adapter = BGEM3Embedding()
    adapter._model = _FakeModel()  # bypass lazy load
    result = await adapter.embed("hello")
    assert isinstance(result, Embedding)
    assert result.dense == [1.0, 2.0]
    assert result.sparse == {0: 0.5, 1: 0.3}
