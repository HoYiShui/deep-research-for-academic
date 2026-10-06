"""phase command: run a single pipeline phase from a validated state."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from application.phase_executor import ExecutionContext, PhaseExecutor
from application.phase_workers import plan_worker
from application.records import DEVELOPMENT_USER_ID
from cli import output
from cli.phase_state import load_phase_state, read_json, state_delta
from cli.phase_tools import DebugTools
from domain.research.ids import canonical_hash
from domain.research.phase_contracts import PhaseInput, merge_phase_result


async def run(args) -> int:
    state = load_phase_state(read_json(args.state, "--state"), args.phase)
    workers = {"plan": plan_worker}
    if args.phase not in workers:
        raise output.EnvError(f"Formal {args.phase} worker is not configured")
    before = state.model_dump(mode="json")
    value = PhaseInput.from_state(state)
    config = state.run_metadata.config
    tools = DebugTools(state, fake=args.fake, seed=args.seed)
    events = []

    async def stopping():
        return False

    context = ExecutionContext(
        owner_id=DEVELOPMENT_USER_ID,
        run_id=state.run_id,
        config=config,
        brief_hash=state.brief_hash,
        lease_token=1,
        unit_id="debug-" + canonical_hash(value),
        deadline=datetime.now(UTC) + timedelta(seconds=config.limits.deadline_s),
        cancel_check=stopping,
        invoke=tools.invoke,
        emit=events.append,
    )
    try:
        async with asyncio.timeout(config.limits.deadline_s):
            changes = await PhaseExecutor(workers).execute_phase(value, context)
        post_state = merge_phase_result(state, changes).model_dump(mode="json")
    finally:
        await tools.close()
    result = {
        "phase": args.phase,
        "state": post_state,
        "state_delta": state_delta(before, post_state),
        "events": events,
        "dependency_mode": "fake" if args.fake else "real",
        "debug_usage": tools.usage,
    }
    if args.json:
        output.emit_json("ok", result)
    else:
        for event in events:
            print(output.format_event(event))
        output.emit_human("ok", json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return output.EXIT_SUCCESS
