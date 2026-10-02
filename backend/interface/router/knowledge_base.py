"""HTTP routes for the local knowledge base (search + documents)."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from application.bootstrap import get_container
from interface.dto.knowledge_base import (
    ChunkResponse,
    DocumentListResponse,
    DocumentUploadResponse,
    SearchRequest,
    SearchResponse,
)

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest) -> dict:
    """Retrieve chunks from the local KB via RetrievalPort (embed -> hybrid -> rerank)."""
    retrieval = get_container().retrieval
    chunks = await retrieval.retrieve(body.query, body.kb_id, body.top_k)
    return {
        "chunks": [
            ChunkResponse(chunk_id=c.chunk_id, text=c.text, score=c.score, metadata=c.metadata)
            for c in chunks
        ]
    }


@router.post("/documents", status_code=202, response_model=DocumentUploadResponse)
async def upload_document(file: Annotated[UploadFile, File()]) -> dict:
    """Accept a PDF upload and start the background ingest pipeline."""
    container = get_container()
    document_id = uuid.uuid4().hex
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await file.read())
        path = tmp.name
    asyncio.create_task(container.knowledge_base.ingest(document_id, path))
    return {"document_id": document_id, "status": "processing"}


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents() -> dict:
    """List documents with their progress state."""
    return {"documents": await get_container().knowledge_base.list_documents()}


@router.get("/documents/{document_id}")
async def get_document(document_id: str) -> dict:
    """Return a document's progress state."""
    return await get_container().knowledge_base.get_document(document_id)


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str) -> dict:
    """Delete a document from the progress registry."""
    await get_container().knowledge_base.delete_document(document_id)
    return {"document_id": document_id, "status": "deleted"}
