"""Standalone adapters: mono-v1 MilvusIndex and pre-mono MilvusStore."""

from __future__ import annotations

import os

from domain.ports import Chunk, Embedding
from infrastructure.vector.index import MilvusIndex

__all__ = ["MilvusIndex", "MilvusStore"]


class MilvusStore:
    """Legacy VectorStorePort; not the mono-v1 hybrid index or authorization path.

    MILVUS_URI selects the Standalone service endpoint. Host-side development
    uses ``http://localhost:19530``; the production backend uses the Compose
    service endpoint ``http://milvus:19530``. Falls back to
    MILVUS_HOST/MILVUS_PORT for backward compatibility.
    """

    def __init__(self, uri: str | None = None) -> None:
        self._uri = uri or os.environ.get("MILVUS_URI") or self._default_uri()
        self._client = None

    @staticmethod
    def _default_uri() -> str:
        host = os.environ.get("MILVUS_HOST", "localhost")
        port = os.environ.get("MILVUS_PORT", "19530")
        return f"http://{host}:{port}"

    def _connect(self):
        """Lazy-connect to Milvus."""
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(uri=self._uri)
        return self._client

    async def hybrid_search(self, kb_id: str, embedding: Embedding, top_k: int) -> list[Chunk]:
        """Search a KB partition with a dense vector."""
        results = self._connect().search(
            collection_name=kb_id,
            data=[embedding.dense],
            limit=top_k,
            output_fields=["*"],
        )
        chunks: list[Chunk] = []
        for batch in results:
            for hit in batch:
                entity = hit.get("entity", {})
                chunks.append(
                    Chunk(
                        chunk_id=str(hit.get("id", "")),
                        text=entity.get("text", ""),
                        score=hit.get("distance", 0.0),
                        metadata=entity,
                    )
                )
        return chunks

    async def insert(self, kb_id: str, chunk_id: str, embedding: Embedding, metadata: dict) -> None:
        """Insert one chunk's vector and metadata into a KB partition."""
        self._connect().insert(
            collection_name=kb_id,
            data=[{"id": chunk_id, "vector": embedding.dense, **metadata}],
        )
