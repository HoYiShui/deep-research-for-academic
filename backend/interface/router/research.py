"""HTTP routes for the research entry (session, clarify, pipeline, report)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from application.bootstrap import get_container
from application.sse import sse_format
from interface.dto.research import (
    ClarifyResponse,
    MessageRequest,
    ResearchRequest,
    SessionResponse,
    StatusResponse,
)

router = APIRouter()


@router.post("/research", response_model=SessionResponse)
async def start_research(body: ResearchRequest) -> dict:
    """Create a session; returns status=clarify (no SSE URL yet)."""
    return await get_container().research.start(body.query)


@router.post("/research/{session_id}/messages", response_model=ClarifyResponse)
async def post_message(session_id: str, body: MessageRequest) -> dict:
    """Advance one clarify round; spawn the pipeline when ready."""
    container = get_container()
    result = await container.sessions.clarify_round(session_id, body.content)
    if result["status"] == "ready":
        container.research.spawn_pipeline(session_id, result.get("brief", {}))
        result["sse_url"] = f"/research/{session_id}/events"
    return result


@router.get("/research/{session_id}/events")
async def stream_events(session_id: str) -> StreamingResponse:
    """Stream SSE events for a session."""
    queue = get_container().bus.queue(session_id)

    async def generator():
        while True:
            event = await queue.get()
            yield sse_format(event)

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.get("/research/{session_id}/report")
async def get_report(session_id: str) -> dict:
    """Return the final report, or 404 if the pipeline has not completed."""
    report = await get_container().research.get_report(session_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not ready")
    return report


@router.get("/research/{session_id}", response_model=StatusResponse)
async def get_status(session_id: str) -> dict:
    """Return the current status by recovering the latest phase snapshot."""
    return await get_container().research.get_status(session_id)


@router.post("/research/{session_id}/cancel")
async def cancel(session_id: str) -> dict:
    """Set the cancellation flag; the orchestrator stops at the next phase boundary."""
    get_container().research.cancel(session_id)
    return {"session_id": session_id, "status": "cancelling"}
