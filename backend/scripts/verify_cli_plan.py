"""Exercise the CLI process from a public frozen Brief; no persisted Run.

Default controlled mode; --real spends model tokens. --record saves the public
state/result as an exclusive new evidence file; never stores keys or prompts.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from application.settings import Settings
from domain.research.models import ResearchBrief, SourceSelection
from domain.research.state import PipelineState


def save_record(path, record):
    with Path(path).open("x") as file:
        json.dump(record, file, ensure_ascii=False, indent=2)


async def verify(args):
    data = json.loads(Path(args.brief).read_text())
    brief = ResearchBrief.model_validate(data.get("brief", data))
    settings = Settings.load()
    selection = SourceSelection(categories=args.sources.split(","))
    state = PipelineState.initial(
        session_id=uuid4(),
        run_id=uuid4(),
        brief_version=1,
        research_brief=brief,
        source_selection=selection,
        config=settings.run_config_snapshot(categories=selection.categories),
    )
    with tempfile.TemporaryDirectory(prefix="dr4a-cli-plan-") as temporary:
        path = Path(temporary) / "state.json"
        path.write_text(state.model_dump_json())
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "cli",
            "phase",
            "plan",
            "--state",
            str(path),
            "--json",
            *(["--real"] if args.real else []),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=settings.run_deadline_s + 10
            )
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
    result = json.loads(stdout)
    if process.returncode == 0:
        post = PipelineState.model_validate(result["state"])
        assert post.phase == "plan" and post.final_report is None
        assert post.research_brief == state.research_brief and len(post.section_plans) == 5
        assert set(result["state_delta"]) == {"section_plans"}
    record = {
        "scope": "isolated_cli_plan_only",
        "persisted": False,
        "input_state": state.model_dump(mode="json"),
        "result": result,
    }
    if args.record:
        await asyncio.to_thread(save_record, args.record, record)
    print(
        json.dumps(
            {
                "scope": "isolated_cli_plan_only",
                "persisted": False,
                "exit_code": process.returncode,
                "status": result["status"],
                "error": result["error"],
                "debug_usage": result.get("debug_usage"),
                "section_count": len(result.get("state", {}).get("section_plans", [])),
            },
            ensure_ascii=False,
        )
    )
    return process.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True)
    parser.add_argument("--real", action="store_true")
    parser.add_argument(
        "--sources", default="papers,web", help="frozen public categories: papers,web"
    )
    parser.add_argument("--record", help="exclusive new public evidence JSON path")
    return asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
