"""Knowledge-base ingest: parse -> chunk -> embed -> store (write path).

The write path uses EmbeddingPort + VectorStorePort directly (read/write
asymmetry: reads go through RetrievalPort, writes do not). Document progress
is persisted via DocumentStorePort so the upload endpoint can be polled.
"""

from __future__ import annotations

from typing import Any

from application.ports import DocumentStorePort
from domain.ports import EmbeddingPort, VectorStorePort


class KnowledgeBaseService:
    """Ingest PDFs into the local KB and track document progress."""

    def __init__(
        self,
        parser: Any,
        embedding: EmbeddingPort,
        vector: VectorStorePort,
        documents: DocumentStorePort,
    ) -> None:
        self._parser = parser
        self._embedding = embedding
        self._vector = vector
        self._documents = documents

    async def ingest(self, document_id: str, path: str, kb_id: str = "default") -> dict:
        """Ingest one document: parse -> chunk -> embed -> store.

        Args:
            document_id: The document id (used as the chunk-id prefix).
            path: The local path to the uploaded file.
            kb_id: The knowledge base partition to store into.

        Returns:
            {"document_id", "status", "chunks"} with status done/failed.
        """
        await self._documents.save(document_id, {"status": "processing", "progress": 0.0})
        try:
            parsed = await self._parser.parse(path)
            chunks = _chunk(parsed.get("text", ""))
            total = max(len(chunks), 1)
            for i, text in enumerate(chunks):
                embedding = await self._embedding.embed(text)
                await self._vector.insert(
                    kb_id,
                    f"{document_id}-{i}",
                    embedding,
                    {"text": text, "document_id": document_id},
                )
                await self._documents.save(
                    document_id, {"status": "processing", "progress": (i + 1) / total}
                )
            await self._documents.save(
                document_id, {"status": "done", "progress": 1.0, "chunks": len(chunks)}
            )
            return {"document_id": document_id, "status": "done", "chunks": len(chunks)}
        except Exception as exc:  # noqa: BLE001 — persist failure, don't crash the endpoint
            await self._documents.save(
                document_id, {"status": "failed", "error": str(exc)}
            )
            return {"document_id": document_id, "status": "failed", "error": str(exc)}

    async def get_document(self, document_id: str) -> dict:
        """Return a document's progress state."""
        state = await self._documents.load(document_id)
        return {"document_id": document_id, **(state or {})}

    async def list_documents(self) -> list[dict]:
        """List all documents with their progress state."""
        return await self._documents.list()

    async def delete_document(self, document_id: str) -> None:
        """Remove a document from the progress registry."""
        await self._documents.delete(document_id)

    async def recover_stale(self) -> None:
        """Mark documents stuck in 'processing' as failed (startup crash recovery)."""
        for doc in await self._documents.list():
            if doc.get("status") == "processing":
                await self._documents.save(doc["document_id"], {"status": "failed", "error": "interrupted"})


def _chunk(text: str, size: int = 800) -> list[str]:
    """Split text into paragraph-preserving chunks of at most ``size`` chars."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > size:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return chunks
