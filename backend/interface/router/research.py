"""HTTP routes for the research entry (walking skeleton)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from application.bootstrap import get_container
from application.sse import sse_format

router = APIRouter()


@router.post("/research")
async def start_research() -> dict:
    """Create a session; returns status=clarify (no SSE URL yet)."""
    return await get_container().research.start()


@router.post("/research/{session_id}/messages")
async def post_message(session_id: str, body: dict) -> dict:
    """Advance one clarify round; spawn the pipeline when ready."""
    container = get_container()
    result = await container.sessions.clarify_round(session_id, body.get("content", ""))
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
