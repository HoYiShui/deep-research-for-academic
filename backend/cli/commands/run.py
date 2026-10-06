"""run command: execute the pipeline from a frozen ResearchBrief."""

from __future__ import annotations

import asyncio
import json

from cli import container, output
from cli.env import load_backend_env
from cli.phase_state import read_json, validate_brief


async def run(args) -> int:
    raw_brief = read_json(args.brief, "--brief")
    if not args.fake:
        from cli.run_real import run as real_run

        return await real_run(args, raw_brief)
    load_backend_env()
    c = container.build_container(fake=args.fake, seed=args.seed, verbose=args.verbose)
    brief = validate_brief(raw_brief)
    start = await c.research.start()
    session_id = start["session_id"]
    # A CLI run is explicitly after Clarify. Persist the supplied frozen input so
    # real-mode snapshots and dump have a complete, traceable session.
    await c.store.save_brief(session_id, brief, brief["task_type"])
    await c.store.set_session_status(session_id, "ready")
    task = c.research.spawn_pipeline(session_id, brief)

    await asyncio.wait_for(task, timeout=300)

    report = await c.research.get_report(session_id)
    events = [output.event_to_dict(e) for e in output.drain_events(c.bus, session_id)]

    if args.json:
        payload: dict = (
            {"final_report": report} if report is not None else {"error": "no report produced"}
        )
        if not args.quiet:
            payload["events"] = events
        payload["dependency_mode"] = "legacy_fake"
        output.emit_json("ok" if report is not None else "failed", payload)
    else:
        if not args.quiet:
            for ev in events:
                print(output.format_event(ev))
        if report is None:
            output.emit_human("failed", "no report produced")
        else:
            output.emit_human("ok", json.dumps(report, ensure_ascii=False, indent=2))
    return output.EXIT_SUCCESS if report is not None else output.EXIT_FAILURE
