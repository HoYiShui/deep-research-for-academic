"""phase command: run a single pipeline phase from a validated state."""

from __future__ import annotations

import json
from dataclasses import asdict

from application.orchestrator import Orchestrator
from cli import container, output
from cli.phase_state import load_phase_state, read_json, state_delta
from infrastructure.storage.memory import InMemoryCancel


async def run(args) -> int:
    state = load_phase_state(read_json(args.state, "--state"), args.phase)
    session_id = state.session_id or "phase"
    before = asdict(state)

    c = container.build_container(fake=args.fake, seed=args.seed, verbose=args.verbose)
    orchestrator = Orchestrator(
        c.bus, InMemoryCancel(), c.store, c.llm, c.search, c.retrieval, c.execution
    )
    # Reuse the orchestrator's single-phase dispatch (no parallel agent path).
    await orchestrator.run_phase(state)

    post_state = asdict(state)
    events = [output.event_to_dict(e) for e in output.drain_events(c.bus, session_id)]
    result = {
        "phase": args.phase,
        "state": post_state,
        "state_delta": state_delta(before, post_state),
        "events": events,
    }
    if args.json:
        output.emit_json("ok", result)
    else:
        for event in events:
            print(output.format_event(event))
        output.emit_human("ok", json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return output.EXIT_SUCCESS
