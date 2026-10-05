"""HTTP routes for the research entry (session, clarify, pipeline, report)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import TypeAdapter

from application.bootstrap import get_container
from application.errors import AppError
from interface.deps import require_user
from interface.dto.research import (
    AskResponse,
    ClarifyResponse,
    ConfirmRequest,
    EmptyRequest,
    MessageRequest,
    ReadyResponse,
    ResearchRequest,
)

router = APIRouter()
_clarify = TypeAdapter(ClarifyResponse)
_confirmation = TypeAdapter(AskResponse | ReadyResponse)


async def request_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> str:
    if not value.strip():
        raise AppError("validation_error", "Idempotency-Key cannot be blank")
    return value.strip()


@router.post("/research", status_code=201, response_model=ClarifyResponse)
async def start_research(
    body: ResearchRequest,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
) -> JSONResponse:
    """Create and initially assess; never start the pipeline before confirmation."""
    result = await get_container(request).research.start(UUID(user), body, key)
    body = _clarify.validate_python(result.body).model_dump(mode="json")
    return JSONResponse(status_code=result.status_code, content=body)


@router.post("/research/{session_id}/messages", response_model=ClarifyResponse)
async def post_message(
    session_id: UUID,
    body: MessageRequest,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
) -> JSONResponse:
    result = await get_container(request).research.message(UUID(user), session_id, body, key)
    body = _clarify.validate_python(result.body).model_dump(mode="json")
    return JSONResponse(status_code=result.status_code, content=body)


@router.post(
    "/research/{session_id}/confirm",
    response_model=AskResponse | ReadyResponse,
    responses={202: {"model": ReadyResponse}},
)
async def confirm(
    session_id: UUID,
    body: ConfirmRequest,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
) -> JSONResponse:
    result = await get_container(request).research.confirm(UUID(user), session_id, body, key)
    body = _confirmation.validate_python(result.body).model_dump(mode="json")
    return JSONResponse(status_code=result.status_code, content=body)


@router.get("/research/{session_id}/events")
async def stream_events(
    session_id: UUID, request: Request, user: str = Depends(require_user)
) -> StreamingResponse:
    """Stream SSE events for a session."""
    view = await get_container(request).research_queries.session_view(UUID(user), session_id)
    if view["run_id"] is None:
        raise AppError("invalid_session_state", "Events require a frozen brief")
    raise AppError("service_not_ready", "Durable run events are not ready")


@router.get("/research/{session_id}/report")
async def get_report(session_id: UUID, request: Request, user: str = Depends(require_user)) -> dict:
    """Authorize first; an unpublished report is a 409, never a fabricated report."""
    view = await get_container(request).research_queries.session_view(UUID(user), session_id)
    if view["status"] != "completed":
        raise AppError("report_not_ready", "Report has not been published")
    raise AppError("service_not_ready", "Published report reader is not ready")


@router.get("/research/{session_id}")
async def get_status(session_id: UUID, request: Request, user: str = Depends(require_user)) -> dict:
    """Project committed owner-scoped state, never synthesize a missing session."""
    queries = getattr(get_container(request), "research_queries", None)
    if queries is None:
        raise AppError("service_not_ready", "Research repositories are not ready", retryable=True)
    return await queries.session_view(UUID(user), session_id)


@router.post("/research/{session_id}/cancel")
async def cancel(
    session_id: UUID,
    body: EmptyRequest,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
) -> dict:
    """Durable cancellation is staged next; never mutate the legacy memory flag."""
    await get_container(request).research_queries.session_view(UUID(user), session_id)
    raise AppError("service_not_ready", "Durable cancellation is not ready")
