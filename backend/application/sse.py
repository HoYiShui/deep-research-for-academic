"""SSE event bus and serialization.

V1 single-process: a process-local dict maps session_id to an asyncio.Queue.
The orchestrator emits events; the SSE endpoint drains them.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

_queues: dict[str, asyncio.Queue] = {}

# Maps event class name -> wire event name for the SSE contract.
_EVENT_NAMES = {
    "PhaseEvent": "phase",
    "StepEvent": "progress",
    "ReworkEvent": "rework",
    "ErrorEvent": "error",
    "DoneEvent": "done",
}


class EventBus:
    """In-process event bus keyed by session_id."""

    def queue(self, session_id: str) -> asyncio.Queue:
        """Return (creating if needed) the queue for a session."""
        if session_id not in _queues:
            _queues[session_id] = asyncio.Queue()
        return _queues[session_id]

    def emit(self, session_id: str, event: Any) -> None:
        """Push an event into a session's queue (fire-and-forget)."""
        self.queue(session_id).put_nowait(event)


def sse_format(event: Any) -> str:
    """Serialize a domain event to an SSE frame.

    Args:
        event: A domain event dataclass.

    Returns:
        A ``data: {...}\n\n`` frame.
    """
    payload = {"event": _EVENT_NAMES.get(type(event).__name__, "event")}
    payload.update(getattr(event, "__dict__", {}))
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
