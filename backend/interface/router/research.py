"""HTTP routes for the research entry (session, clarify, pipeline, report)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import TypeAdapter

from application.bootstrap import get_container
from application.errors import AppError
from interface.deps import require_user
from interface.dto.research import (
    AskResponse,
    CancelResponse,
    ClarifyResponse,
    ConfirmRequest,
    EmptyRequest,
    MessageRequest,
    ReadyResponse,
    ResearchRequest,
    ResumeRequest,
)

router = APIRouter()
_clarify = TypeAdapter(ClarifyResponse)
_confirmation = TypeAdapter(AskResponse | ReadyResponse)


class _RunStreamingResponse(StreamingResponse):
    """Also unsubscribe when disconnect cancels the ASGI send after a yield."""

    def __init__(self, stream):
        self.stream = stream
        super().__init__(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.stream.aclose()


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
    stream = await get_container(request).run_events.open(UUID(user), session_id)
    return _RunStreamingResponse(stream)


@router.get("/research/{session_id}/report")
async def get_report(session_id: UUID, request: Request, user: str = Depends(require_user)) -> dict:
    """Authorize first; an unpublished report is a 409, never a fabricated report."""
    return await get_container(request).research_queries.report_view(UUID(user), session_id)


@router.get("/research/{session_id}/artifacts/{artifact_id}/files/{file_name}")
async def get_artifact(
    session_id: UUID,
    artifact_id: str,
    file_name: str,
    request: Request,
    user: str = Depends(require_user),
) -> Response:
    service = getattr(get_container(request), "research_artifacts", None)
    if service is None:
        raise AppError("service_not_ready", "Attachment service is not ready", retryable=True)
    body, media_type = await service.download(UUID(user), session_id, artifact_id, file_name)
    return Response(
        body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/research/{session_id}")
async def get_status(session_id: UUID, request: Request, user: str = Depends(require_user)) -> dict:
    """Project committed owner-scoped state, never synthesize a missing session."""
    queries = getattr(get_container(request), "research_queries", None)
    if queries is None:
        raise AppError("service_not_ready", "Research repositories are not ready", retryable=True)
    return await queries.session_view(UUID(user), session_id)


@router.post("/research/{session_id}/cancel", response_model=CancelResponse)
async def cancel(
    session_id: UUID,
    body: EmptyRequest,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
) -> JSONResponse:
    result = await get_container(request).research.cancel(UUID(user), session_id, key)
    return JSONResponse(
        status_code=result.status_code,
        content=CancelResponse.model_validate(result.body).model_dump(mode="json"),
    )


@router.post("/research/{session_id}/resume", status_code=202, response_model=ReadyResponse)
async def resume(
    session_id: UUID,
    body: ResumeRequest,
    request: Request,
    user: str = Depends(require_user),
    key: str = Depends(request_key),
) -> JSONResponse:
    result = await get_container(request).research.resume(UUID(user), session_id, body, key)
    return JSONResponse(
        status_code=result.status_code,
        content=ReadyResponse.model_validate(result.body).model_dump(mode="json"),
    )
