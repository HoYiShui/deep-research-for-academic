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
