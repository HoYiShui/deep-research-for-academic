"""Knowledge-base DTOs (request/response models)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """POST /knowledge-base/search body."""

    query: str = Field(min_length=1)
    kb_id: str = "default"
    top_k: int = Field(default=20, ge=1, le=100)
    rerank: bool = True


class ChunkResponse(BaseModel):
    """A retrieved chunk."""

    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """POST /knowledge-base/search response."""

    chunks: list[ChunkResponse]


class DocumentUploadResponse(BaseModel):
    """POST /knowledge-base/documents response."""

    document_id: str
    status: str


class DocumentListItem(BaseModel):
    """A document's progress state."""

    document_id: str
    status: str
    progress: float = 0.0


class DocumentListResponse(BaseModel):
    """GET /knowledge-base/documents response."""

    documents: list[DocumentListItem]
