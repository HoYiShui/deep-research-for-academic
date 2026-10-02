"""HTTP routes for the local knowledge base (search)."""

from __future__ import annotations

from fastapi import APIRouter

from application.bootstrap import get_container

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


@router.post("/search")
async def search(body: dict) -> dict:
    """Retrieve chunks from the local KB via RetrievalPort (embed -> hybrid -> rerank)."""
    retrieval = get_container().retrieval
    chunks = await retrieval.retrieve(
        body.get("query", ""), body.get("kb_id", "default"), body.get("top_k", 20)
    )
    return {
        "chunks": [
            {"chunk_id": c.chunk_id, "text": c.text, "score": c.score, "metadata": c.metadata}
            for c in chunks
        ]
    }
