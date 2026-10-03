"""run command: full pipeline (clarify -> pipeline -> report)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

from cli import container, output


def _load_env() -> None:
    path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


def _load_answers(path: str) -> list[str]:
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _read_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


async def run(args) -> int:
    if not args.query and not args.brief_file:
        raise output.UsageError("query or --brief-file is required")
    if args.query and args.brief_file:
        raise output.UsageError("give query or --brief-file, not both")

    _load_env()
    c = container.build_container(fake=args.fake, seed=args.seed)

    if args.brief_file:
        brief = _read_json(args.brief_file)
        session_id = uuid.uuid4().hex
        task = c.research.spawn_pipeline(session_id, brief)
    else:
        start = await c.research.start(args.query)
        session_id = start["session_id"]
        answers = _load_answers(args.answers) if args.answers else []
        max_rounds = args.max_iterations or 3
        result = {"status": "ask"}
        for i in range(max_rounds):
            answer = answers[i] if i < len(answers) else "Use reasonable defaults and proceed."
            result = await c.sessions.clarify_round(session_id, args.query if i == 0 else answer)
            if result["status"] == "ready":
                break
        if result["status"] != "ready":
            raise output.UsageError("clarify did not reach ready; use --brief-file or --answers")
        task = c.research.spawn_pipeline(session_id, result["brief"])

    await asyncio.wait_for(task, timeout=300)

    report = await c.research.get_report(session_id)
    if report is None:
        if args.json:
            output.emit_json("failed", {"error": "no report produced"})
        else:
            output.emit_human("failed", "no report produced")
        return output.EXIT_FAILURE

    if args.json:
        output.emit_json("ok", {"final_report": report})
    else:
        output.emit_human("ok", json.dumps(report, ensure_ascii=False, indent=2))
    return output.EXIT_SUCCESS
