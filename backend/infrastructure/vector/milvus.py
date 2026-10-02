"""Milvus vector store adapter (hybrid search + insert, partition per KB)."""

from __future__ import annotations

import os

from domain.ports import Chunk, Embedding


class MilvusStore:
    """VectorStorePort implementation backed by Milvus.

    MILVUS_URI selects the backend: a file path (e.g. ``./milvus.db``) uses the
    embedded Milvus Lite in dev; an ``http://host:port`` URI uses a standalone
    server in prod. Falls back to MILVUS_HOST/MILVUS_PORT for backward compat.
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
