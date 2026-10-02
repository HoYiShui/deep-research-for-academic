"""BGE-M3 embedding adapter (dense + sparse, lazy-loaded local model)."""

from __future__ import annotations

import os

from domain.ports import Embedding


class BGEM3Embedding:
    """EmbeddingPort implementation via a local BGE-M3 model.

    BGE-M3 emits a dense vector (semantics) and a sparse lexical-weight vector
    (exact terms), used for Milvus hybrid search with RRF fusion.
    """

    def __init__(self, model_name: str | None = None) -> None:
        # A local weights dir overrides the HF hub name (BGE_M3_MODEL_PATH).
        self._model_name = model_name or os.environ.get("BGE_M3_MODEL_PATH", "BAAI/bge-m3")
        self._model = None

    def _load(self):
        """Lazy-load the model to keep the module import cheap."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    async def embed(self, text: str) -> Embedding:
        """Embed text into a dual vector (dense + sparse lexical weights)."""
        out = self._load().encode([text], return_dense=True, return_sparse=True)
        dense = out["dense_vecs"][0].tolist()
        sparse = dict(out["lexical_weights"][0])
        return Embedding(dense=dense, sparse=sparse)
