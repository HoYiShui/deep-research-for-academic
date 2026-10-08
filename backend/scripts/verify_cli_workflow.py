"""Exercise the real frozen-brief CLI and audit its persisted final checkpoint.

This spends configured model/search credits and creates a Session/Run. It does
not approve a conversational Brief, migrate databases, or accept report quality.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from application.settings import Settings
from domain.research.models import ResearchBrief
from domain.research.reporting import validate_publication
from domain.research.state import PipelineState
from scripts.verify_cli_plan import save_record


def audit_checkpoint(result, snapshot, brief):
    """Audit identity and deterministic delivery, not research quality."""
    state = PipelineState.model_validate(snapshot["state"])
    checks = {
        "same_session": result.get("session_id")
        == snapshot.get("session_id")
        == str(state.session_id),
        "same_run": result.get("run_id") == snapshot.get("run_id") == str(state.run_id),
        "same_checkpoint": result.get("checkpoint_seq") == snapshot.get("checkpoint_seq")
        and type(snapshot.get("checkpoint_seq")) is int
        and snapshot["checkpoint_seq"] > 0,
        "same_phase": result.get("phase") == snapshot.get("phase") == state.phase,
        "frozen_brief_matches": state.research_brief == brief,
        "done": state.phase == "done",
        "reviewed_current_version": state.reviewed_draft_version == state.draft_version > 0,
        "five_chapters": set(state.draft_sections) == {f"section_{i}" for i in range(1, 6)},
        "published_report": state.final_report is not None,
        "same_report": state.final_report is not None
        and result.get("final_report") == state.final_report.model_dump(mode="json"),
        "canonical_report": False,
    }
    if state.final_report is not None:
        try:
            before = PipelineState.model_validate(
                state.model_dump() | {"phase": "review", "final_report": None}
            )
            validate_publication(before, state.final_report)
            checks["canonical_report"] = True
        except ValueError:
            pass
    return checks


async def invoke(arguments, timeout):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cli",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def diagnostics():
        async for line in process.stderr:
            # CLI verbose diagnostics contain coordinator metadata, not prompts.
            sys.stderr.write(line.decode())
            sys.stderr.flush()

    task = asyncio.create_task(diagnostics())
    try:
        async with asyncio.timeout(timeout):
            stdout = await process.stdout.read()
            await process.wait()
            await task
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return process.returncode, json.loads(stdout)


async def verify(args):
    settings = Settings.load()
    raw = json.loads(Path(args.brief).read_text())
    brief = ResearchBrief.model_validate(raw)
    if args.record and Path(args.record).exists():
        raise ValueError("Record already exists; use a new evidence path")
    extra = ["--debug-db"] if args.debug_db else []
    code, result = await invoke(
        [
            "run",
            "--brief",
            args.brief,
            "--sources",
            args.sources,
            "--real",
            "--json",
            "--verbose",
            *extra,
        ],
        settings.run_deadline_s + settings.shutdown_s + 30,
    )
    snapshot = None
    checks = {"command_succeeded": code == 0}
    if result.get("session_id"):
        dump_code, snapshot = await invoke(["dump", result["session_id"], "--json", *extra], 30)
        checks["checkpoint_readable"] = dump_code == 0
        if dump_code == 0:
            checks.update(audit_checkpoint(result, snapshot, brief))
    record = {
        "scope": "real_frozen_cli_workflow",
        "quality_accepted": False,
        "brief": raw,
        "checks": checks,
        "result": result,
        "snapshot": snapshot,
    }
    if args.record:
        await asyncio.to_thread(save_record, args.record, record)
    state = snapshot.get("state", {}) if snapshot and snapshot.get("error") is None else {}
    print(
        json.dumps(
            {
                "scope": record["scope"],
                "quality_accepted": False,
                "checks": checks,
                "session_id": result.get("session_id"),
                "run_id": result.get("run_id"),
                "phase": result.get("phase"),
                "error": result.get("error"),
                "review_verdict": state.get("review_verdict"),
                "fact_counts": {
                    name: len(state.get(name, {}))
                    for name in (
                        "sources",
                        "evidence",
                        "claims",
                        "quantitative_observations",
                        "analysis_artifacts",
                    )
                },
            },
            ensure_ascii=False,
        )
    )
    return 0 if all(checks.values()) else code or 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True)
    parser.add_argument(
        "--real",
        action="store_true",
        required=True,
        help="explicitly authorize paid model/search calls",
    )
    parser.add_argument("--sources", default="papers,web")
    parser.add_argument("--debug-db", action="store_true")
    parser.add_argument("--record", help="exclusive public evidence JSON path")
    return asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
