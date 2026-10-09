"""Lab runner: drive the real research pipeline end to end, in memory, fully traced.

Reuses the canonical agents, workers, merge and Machine policy, but none of the
durable harness (PG ledger, leases, uncertain calls, budget reservation). Every
unit's state, every model prompt/response and every real exception message is
written under the output directory so a failed run can be diagnosed and resumed.

    uv run python -m lab.run --brief lab/briefs/case1.json
    uv run python -m lab.run --state .local/lab/<run>/state.json   # resume
"""

import argparse
import asyncio
import json
import os
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pydantic import SecretStr

from application.phase_executor import ExecutionContext, PhaseExecutor
from application.phase_units import plan_units
from application.phase_workers import public_workers
from application.records import DEVELOPMENT_USER_ID
from application.settings import Settings
from cli.phase_tools import DebugTools
from cli.trace import TraceRecorder
from domain.research.agents import prompt_versions
from domain.research.diagnostics import diagnostic_scope
from domain.research.machine import apply_pipeline_decision, decide_pipeline
from domain.research.models import SourceSelection
from domain.research.phase_contracts import PhaseInput, merge_phase_result
from domain.research.reporting import build_report
from domain.research.state import PipelineState
from infrastructure.parser.html import LIGHT_PARSER_VERSION

# Generous lab limits: find the workflow's real cost before tuning budgets.
LAB_LIMITS = {
    "run_deadline_s": 7200,
    "run_search_calls": 200,
    "run_fetch_calls": 200,
    "run_llm_calls": 400,
    "run_tokens": 5_000_000,
    "run_terminal_reserved_calls": 8,
    "run_terminal_reserved_tokens": 100_000,
    "llm_timeout_s": 300,
    "search_timeout_s": 30,
    "fetch_timeout_s": 60,
}


class Lab:
    def __init__(self, out: Path, trace: TraceRecorder):
        self.out, self.trace = out, trace
        self.seq = 0
        self.started = monotonic()

    def log(self, message):
        line = f"[{datetime.now().astimezone().strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        with (self.out / "lab.log").open("a") as file:
            file.write(line + "\n")
        self.trace({"event": "lab", "message": message})

    def save(self, state, label):
        self.seq += 1
        data = state.model_dump(mode="json")
        text = json.dumps(data, ensure_ascii=False, indent=1)
        (self.out / "states").mkdir(exist_ok=True)
        (self.out / "states" / f"{self.seq:03d}-{label}.json").write_text(text)
        (self.out / "state.json").write_text(text)

    def fail(self, where, exc):
        detail = "".join(traceback.format_exception(exc))
        (self.out / "error.txt").write_text(f"{where}\n\n{detail}")
        self.trace({"event": "lab_failed", "where": where}, content={"traceback": detail})
        self.log(f"FAILED at {where}: {type(exc).__name__}: {exc}")


def _add_usage(state, usage, elapsed):
    used = state.run_metadata.budget_used
    values = {
        "llm_calls": used.llm_calls + usage["llm_calls"],
        "search_calls": used.search_calls + usage["search_calls"],
        "fetch_calls": used.fetch_calls + usage["fetch_calls"],
        "tokens": used.tokens + usage["input_tokens"] + usage["output_tokens"],
        "elapsed_s": used.elapsed_s + int(elapsed),
    }
    metadata = state.run_metadata.model_dump() | {"budget_used": values}
    return PipelineState.model_validate(state.model_dump() | {"run_metadata": metadata})


async def run_phase(lab, state, deadline):
    phase = state.phase
    tools = DebugTools(state, fake=False)
    units = plan_units(state)

    async def invoke(operation, payload):
        # The production adapters flatten foreign errors; the lab keeps them.
        try:
            return await tools.invoke(operation, payload)
        except Exception as exc:
            lab.trace(
                {"event": "lab_tool_exception", "tool": operation, "error": repr(exc)[:2000]},
                content={"traceback": "".join(traceback.format_exception(exc))},
            )
            raise

    lab.log(f"phase={phase} units={len(units)}")
    try:
        for index, unit in enumerate(units, 1):
            label = unit.parameters.get("query") or ",".join(unit.section_ids) or unit.unit_id
            lab.log(f"  unit {index}/{len(units)} {unit.parameters.get('kind')} {label[:90]}")
            tools.for_unit(unit)

            async def never():
                return False

            context = ExecutionContext(
                owner_id=DEVELOPMENT_USER_ID,
                run_id=state.run_id,
                config=state.run_metadata.config,
                brief_hash=state.brief_hash,
                lease_token=1,
                unit_id=unit.unit_id,
                deadline=deadline,
                cancel_check=never,
                invoke=invoke,
                emit=lambda event: None,
                unit=unit,
            )
            before = dict(tools.usage)
            started = monotonic()
            with diagnostic_scope(phase=phase, unit_id=unit.unit_id, section_ids=unit.section_ids):
                result = await PhaseExecutor(public_workers()).execute_phase(
                    PhaseInput.from_state(state), context
                )
            state = merge_phase_result(
                state,
                result,
                target_sections=unit.section_ids,
                target_requirements=unit.requirement_ids or None,
            )
            delta = {
                key: tools.usage[key] - before.get(key, 0)
                for key in before
                if key
                in ("llm_calls", "search_calls", "fetch_calls", "input_tokens", "output_tokens")
            }
            state = _add_usage(state, delta, monotonic() - started)
            lab.save(state, f"{phase}-{index:02d}")
            lab.log(f"    ok {monotonic() - started:.0f}s usage={delta}")
    finally:
        await tools.close()
    return state


def recompute_coverage(state):
    """Re-derive coverage after a coverage-rule change, without re-researching."""
    from domain.research.agents.coverage import section_coverage

    data = state.model_dump()
    claims = dict(state.claims)
    all_specs = {spec.spec_id: spec for plan in state.section_plans for spec in plan.claim_specs}
    coverage = {}
    for plan in state.section_plans:
        updates, coverage[plan.section_id] = section_coverage(
            plan,
            claims,
            state.evidence,
            state.sources,
            state.claim_evidence_links,
            all_specs=all_specs,
        )
        claims.update(updates)
    data["claims"] = {key: value.model_dump() for key, value in claims.items()}
    data["section_coverage"] = {key: value.model_dump() for key, value in coverage.items()}
    return PipelineState.model_validate(data)


def summarize(state):
    return {
        "sections": len(state.section_plans),
        "sources": len(state.sources),
        "evidence": len(state.evidence),
        "claims": len(state.claims),
        "links": len(state.claim_evidence_links),
        "drafts": len(state.draft_sections),
        "issues": len([i for i in state.critic_feedback if not i.resolved]),
        "verdict": state.review_verdict,
        "rework": state.run_metadata.rework_count,
        "budget": state.run_metadata.budget_used.model_dump(),
    }


async def main(args):
    if os.environ.get("CLAUDECODE"):
        # A coding-agent parent exports its own Anthropic endpoint/token, which
        # would outrank backend/.env and silently send the DR4A key elsewhere.
        for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
            if os.environ.pop(name, None) is not None:
                print(f"lab: ignoring inherited {name} from the coding-agent environment")
    settings = Settings.load()
    # DebugTools reloads Settings itself, so tool choices must go through env.
    os.environ["WEB_SEARCH_PROVIDER"] = args.search
    overrides = dict(LAB_LIMITS)
    if settings.parser_version == "unconfigured":
        overrides["parser_version"] = LIGHT_PARSER_VERSION
    settings = settings.model_copy(update=overrides)

    if args.state:
        state = PipelineState.model_validate_json(Path(args.state).read_text())
        out = Path(args.state).parent
        current = prompt_versions()
        if state.run_metadata.config.versions.prompt_versions != current:
            # The lab exists to iterate prompts on saved facts; production
            # resume keeps refusing a silent prompt change.
            data = state.model_dump()
            data["run_metadata"]["config"]["versions"]["prompt_versions"] = current
            state = PipelineState.model_validate(data)
            print("lab: refreshed prompt_versions on the resumed state")
        if args.recompute_coverage:
            state = recompute_coverage(state)
            print("lab: recomputed claim status and section coverage from saved facts")
    else:
        brief = json.loads(Path(args.brief).read_text())
        selection = SourceSelection(categories=args.sources.split(","), knowledge_base_ids=[])
        state = PipelineState.initial(
            session_id=uuid4(),
            run_id=uuid4(),
            brief_version=1,
            research_brief=brief,
            source_selection=selection,
            config=settings.run_config_snapshot(categories=selection.categories),
        )
        name = f"{datetime.now().astimezone().strftime('%m%d-%H%M%S')}-{Path(args.brief).stem}"
        out = Path(args.out) / name
        out.mkdir(parents=True)

    secrets = [
        value.get_secret_value()
        for field in type(settings).model_fields
        if isinstance(value := getattr(settings, field), SecretStr)
    ]
    trace = TraceRecorder(
        out / f"trace-{datetime.now().astimezone().strftime('%H%M%S')}.jsonl",
        content=True,
        secrets=secrets,
    )
    lab = Lab(out, trace)
    lab.log(f"out={out} phase={state.phase}")
    if not args.state:
        lab.save(state, "initial")
    deadline = datetime.now(UTC) + timedelta(seconds=settings.run_deadline_s)
    try:
        with diagnostic_scope(sink=trace, run_id=str(state.run_id)):
            while True:
                try:
                    state = await run_phase(lab, state, deadline)
                except Exception as exc:  # noqa: BLE001 -- the lab reports, never hides
                    lab.fail(f"phase {state.phase}", exc)
                    return 1
                lab.log(f"after {state.phase}: {json.dumps(summarize(state), ensure_ascii=False)}")
                finished = state.phase
                try:
                    decision = decide_pipeline(state)
                except Exception as exc:  # noqa: BLE001
                    lab.fail(f"decide after {state.phase}", exc)
                    return 1
                if decision.deliver:
                    try:
                        report = build_report(
                            state, report_id=uuid4(), created_at=datetime.now(UTC)
                        )
                    except Exception as exc:  # noqa: BLE001
                        lab.fail("build_report", exc)
                        return 1
                    state = PipelineState.model_validate(
                        state.model_dump() | {"phase": "done", "final_report": report}
                    )
                    lab.save(state, "done")
                    (out / "report.md").write_text(report.markdown)
                    lab.log(f"DONE verdict={report.review_verdict} -> {out / 'report.md'}")
                    return 0
                lab.log(
                    f"decision next={decision.next_phase} rework={decision.rework_count} "
                    f"stop={decision.stop_reason} targets={len(decision.targets)}"
                )
                state = apply_pipeline_decision(state, decision)
                lab.save(state, f"to-{state.phase}")
                if args.stop_after == finished:
                    lab.log(f"stopped after {finished}; resume with --state {out / 'state.json'}")
                    return 0
    finally:
        trace.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="lab.run")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--brief", help="frozen ResearchBrief JSON")
    source.add_argument("--state", help="resume from a saved lab state.json")
    parser.add_argument("--sources", default="papers,web")
    parser.add_argument("--out", default=".local/lab")
    parser.add_argument("--search", choices=["bocha", "search_router"], default="search_router")
    parser.add_argument(
        "--recompute-coverage", action="store_true", help="re-derive coverage on --state"
    )
    parser.add_argument("--stop-after", choices=["plan", "research", "analyze", "write", "review"])
    raise SystemExit(asyncio.run(main(parser.parse_args())))
