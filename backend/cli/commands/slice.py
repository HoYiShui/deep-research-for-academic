"""slice command: run a single phase on a canned state."""

from __future__ import annotations

import json
from dataclasses import asdict

from application.orchestrator import Orchestrator
from cli import container, output
from domain.research.state import PipelineState
from infrastructure.storage.memory import InMemoryCancel


def _read_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


async def run(args) -> int:
    state = PipelineState(**_read_json(args.input))
    state.phase = args.phase
    session_id = state.session_id or "slice"

    c = container.build_container(fake=args.fake, seed=args.seed, verbose=args.verbose)
    orchestrator = Orchestrator(
        c.bus, InMemoryCancel(), c.store, c.llm, c.search, c.retrieval, c.execution
    )
    # Reuse the orchestrator's single-phase dispatch (no parallel agent path).
    await orchestrator.run_phase(state)

    payload = asdict(state)
    events = [output.event_to_dict(e) for e in output.drain_events(c.bus, session_id)]
    if args.json:
        output.emit_json("ok", {"phase": args.phase, "state": payload, "events": events})
    else:
        for ev in events:
            print(output.format_event(ev))
        output.emit_human("ok", json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return output.EXIT_SUCCESS
