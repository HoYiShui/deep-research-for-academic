"""dump command: read a session's latest snapshot state (real-mode forensics)."""

from __future__ import annotations

import json
import os

from cli import output
from infrastructure.storage.postgres import PostgresStateStore

_PHASES = ["done", "review", "write", "analyze", "research", "plan"]


async def run(args) -> int:
    store = PostgresStateStore(os.environ.get("DATABASE_URL", ""))
    for phase in _PHASES:
        state = await store.load_latest_snapshot(args.session_id, phase)
        if state is not None:
            if args.json:
                output.emit_json("ok", {"phase": phase, "state": state})
            else:
                output.emit_human("ok", json.dumps(state, ensure_ascii=False, indent=2))
            return output.EXIT_SUCCESS
    if args.json:
        output.emit_json("failed", {"error": "session not found"})
    else:
        output.emit_human("failed", "session not found")
    return output.EXIT_FAILURE
