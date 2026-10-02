"""Local-KB retrieval: embed -> hybrid -> rerank (RetrievalPort).

Read path composes EmbeddingPort (query vector) + VectorStorePort (Milvus
hybrid search) + RerankPort (cross-encoder). On Milvus failure the retrieval
degrades to empty (skip local, keep paper/web) and records a non-fatal gap.
"""

from __future__ import annotations

from typing import Any

from domain.ports import Chunk, EmbeddingPort, RerankPort, VectorStorePort


class LocalRetrieval:
    """RetrievalPort implementation composing embed + hybrid + rerank."""

    def __init__(
        self,
        embedding: EmbeddingPort,
        vector: VectorStorePort,
        rerank: RerankPort,
        candidate_k: int = 20,
    ) -> None:
        self._embedding = embedding
        self._vector = vector
        self._rerank = rerank
        self._candidate_k = candidate_k
        self.gaps: list[dict[str, Any]] = []

    async def retrieve(self, query: str, kb_id: str, top_k: int) -> list[Chunk]:
        """Retrieve top-k chunks for a query from the local KB.

        Args:
            query: The search query.
            kb_id: The knowledge base partition.
            top_k: The number of chunks to return after reranking.

        Returns:
            Reranked chunks, or [] if the vector store is unavailable (degrade).
        """
        try:
            embedding = await self._embedding.embed(query)
            candidates = await self._vector.hybrid_search(kb_id, embedding, self._candidate_k)
        except Exception as exc:  # noqa: BLE001 — Milvus down: degrade, not fatal
            self.gaps.append({"source": "milvus", "reason": "unavailable", "detail": str(exc)})
            return []
        if not candidates:
            return []
        return await self._rerank.rerank(query, candidates, top_k)

    def take_gaps(self) -> list[dict[str, Any]]:
        """Return and clear accumulated coverage gaps."""
        gaps, self.gaps = self.gaps, []
        return gaps
