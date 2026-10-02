"""Milvus vector store adapter (hybrid search + insert, partition per KB)."""

from __future__ import annotations

from domain.ports import Chunk, Embedding


class MilvusStore:
    """VectorStorePort implementation backed by Milvus."""

    def __init__(self, host: str = "localhost", port: int = 19530) -> None:
        self._host = host
        self._port = port
        self._client = None

    def _connect(self):
        """Lazy-connect to Milvus."""
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(uri=f"http://{self._host}:{self._port}")
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
