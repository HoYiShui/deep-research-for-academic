"""phase command: run a single pipeline phase from a validated state."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from application.errors import AppError
from application.phase_executor import ExecutionContext, PhaseExecutor
from application.phase_units import plan_units
from application.phase_workers import public_workers
from application.records import DEVELOPMENT_USER_ID
from cli import output
from cli.phase_state import load_phase_state, read_json, state_delta
from cli.phase_tools import DebugTools
from domain.ports import AdapterError
from domain.research.diagnostics import diagnostic_scope
from domain.research.phase_contracts import PhaseInput, merge_phase_result


async def run(args) -> int:
    state = load_phase_state(read_json(args.state, "--state"), args.phase)
    workers = public_workers()
    if args.phase not in workers:
        raise output.EnvError(f"Formal {args.phase} worker is not configured")
    before = state.model_dump(mode="json")
    config = state.run_metadata.config
    tools = DebugTools(state, fake=args.fake, seed=args.seed)
    events = []

    async def stopping():
        return False

    deadline = datetime.now(UTC) + timedelta(seconds=config.limits.deadline_s)
    failure = None
    active_unit = None
    active_scope = None
    completed_units = 0
    total_units = 0

    def progress(stage):
        if getattr(args, "verbose", False) and active_scope is not None:
            # Trusted planner IDs/section enums only. Never dump parameters,
            # worker diagnostics, queries, prompts or provider errors to logs.
            output.log(
                f"debug_unit phase={args.phase} stage={stage} unit_id={active_scope.unit_id} "
                f"sections={','.join(active_scope.section_ids)} "
                f"completed_units={completed_units} total_units={total_units} "
                "persistence=local_only"
            )

    try:
        async with asyncio.timeout(config.limits.deadline_s):
            units = plan_units(state)
            total_units = len(units)
            for unit in units:
                active_scope = unit
                active_unit = unit.unit_id
                value = PhaseInput.from_state(state)
                tools.for_unit(unit)
                context = ExecutionContext(
                    owner_id=DEVELOPMENT_USER_ID,
                    run_id=state.run_id,
                    config=config,
                    brief_hash=state.brief_hash,
                    lease_token=1,
                    unit_id=unit.unit_id,
                    deadline=deadline,
                    cancel_check=stopping,
                    invoke=tools.invoke,
                    emit=events.append,
                    unit=unit,
                )
                progress("started")
                with diagnostic_scope(
                    run_id=str(state.run_id),
                    phase=state.phase,
                    unit_id=unit.unit_id,
                    section_ids=unit.section_ids,
                ):
                    changes = await PhaseExecutor(workers).execute_phase(value, context)
                try:
                    state = merge_phase_result(
                        state,
                        changes,
                        target_sections=unit.section_ids,
                        target_requirements=unit.requirement_ids or None,
                    )
                except ValueError:
                    # This narrow boundary classifies contract/immutable-fact
                    # rejection, not arbitrary worker bugs. Keep the previous
                    # state; exception text can contain source/quote content.
                    raise AppError(
                        "invalid_state", "Phase result failed canonical state merge validation"
                    ) from None
                completed_units += 1
                progress("merged")
                if state.phase == "research":
                    events.append(
                        {
                            "event": "unit",
                            "phase": state.phase,
                            "unit_id": unit.unit_id,
                            "section_ids": unit.section_ids,
                            **unit.parameters,
                        }
                    )
    except (AppError, AdapterError) as exc:
        failure = (
            output.EXIT_ENV if isinstance(exc, AdapterError) else output.EXIT_FAILURE,
            exc.code,
            exc.message,
            exc.retryable,
        )
    except TimeoutError:
        failure = (output.EXIT_FAILURE, "phase_timeout", "Debug phase deadline exceeded", True)
    except asyncio.CancelledError:
        failure = (
            output.EXIT_FAILURE,
            "phase_interrupted",
            "Local debug phase interrupted; no persisted Run was cancelled",
            False,
        )
    finally:
        await tools.close()
    post_state = state.model_dump(mode="json")
    result = {
        "phase": args.phase,
        "state": post_state,
        "state_delta": state_delta(before, post_state),
        "events": events,
        "dependency_mode": "fake" if args.fake else "real",
        "debug_usage": tools.usage,
    }
    if failure is not None:
        progress("failed")
        # Last merged local state, not a persisted checkpoint or a completed unit.
        result["failed_unit_id"] = active_unit
        return output.emit_error(args, *failure, data=result)
    if args.json:
        output.emit_json("ok", result)
    else:
        for event in events:
            print(output.format_event(event))
        output.emit_human("ok", json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return output.EXIT_SUCCESS
