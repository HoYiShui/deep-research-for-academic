"""BGE-reranker adapter (cross-encoder, lazy-loaded)."""

from __future__ import annotations

from domain.ports import Chunk


class BGEReranker:
    """RerankPort implementation via a local BGE-reranker cross-encoder."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        self._model_name = model_name
        self._model = None

    def _load(self):
        """Lazy-load the cross-encoder."""
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)
        return self._model

    async def rerank(self, query: str, candidates: list[Chunk], top_k: int) -> list[Chunk]:
        """Rerank candidates by cross-encoder score and return the top-k."""
        pairs = [(query, c.text) for c in candidates]
        scores = self._load().predict(pairs)
        ranked = sorted(zip(candidates, scores), key=lambda pair: -pair[1])
        return [chunk for chunk, _ in ranked[:top_k]]
