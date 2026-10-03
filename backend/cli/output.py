"""Output contract: exit codes and the stdout/stderr split (agent-friendly).

stdout carries the result body (human-readable, or a single JSON object with
``--json``); stderr carries errors and logs (one line each, timestamped).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

# Exit codes (contracts/cli.md).
EXIT_SUCCESS = 0
EXIT_FAILURE = 1  # research failure
EXIT_USAGE = 2  # usage error
EXIT_ENV = 3  # environment error


class UsageError(Exception):
    """A usage error (exit code 2)."""


class EnvError(Exception):
    """An environment error (exit code 3)."""


def log(message: str) -> None:
    """Write a timestamped log line to stderr."""
    print(f"{time.strftime('%H:%M:%S')} {message}", file=sys.stderr)


def emit_json(status: str, data: dict[str, Any] | None = None) -> None:
    """Write a single JSON result object to stdout."""
    payload: dict[str, Any] = {"status": status}
    if data:
        payload.update(data)
    print(json.dumps(payload, ensure_ascii=False, default=str))


def emit_human(status: str, body: str = "") -> None:
    """Write a human-readable result to stdout."""
    print(f"status: {status}")
    if body:
        print(body)


def drain_events(bus, session_id: str) -> list:
    """Drain all queued events for a session from an EventBus."""
    events = []
    queue = bus.queue(session_id)
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def event_to_dict(event) -> dict:
    """Serialize a domain event to a plain dict for JSON output."""
    return {"event": type(event).__name__, **getattr(event, "__dict__", {})}


def format_event(ev: dict) -> str:
    """Format a serialized event as a one-line string."""
    name = ev.get("event", "event")
    rest = ", ".join(f"{k}={v}" for k, v in ev.items() if k != "event")
    return f"{name}: {rest}" if rest else name
