"""Research DTOs (request/response models)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    """POST /research body."""

    query: str = Field(min_length=1)
    task_type: str | None = None
    sources: list[str] | None = None


class MessageRequest(BaseModel):
    """POST /research/{id}/messages body."""

    content: str = Field(min_length=1)


class SessionResponse(BaseModel):
    """POST /research response."""

    session_id: str
    status: str


class ClarifyResponse(BaseModel):
    """One clarify round's response."""

    status: str
    questions: list[str] = Field(default_factory=list)
    brief: dict[str, Any] = Field(default_factory=dict)
    sse_url: str | None = None


class StatusResponse(BaseModel):
    """GET /research/{id} response."""

    session_id: str
    status: str
    phase: str | None = None
