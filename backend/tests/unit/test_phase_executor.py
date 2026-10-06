"""Shared phase dispatch only; workers are explicit, never a fake fallback."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from application.errors import AppError
from application.phase_executor import ExecutionContext, PhaseExecutor
from domain.research.phase_contracts import PhaseInput, PhaseResult
from tests.unit.test_phase_contracts import plans
from tests.unit.test_state import initial_state


def context(state, *, cancelled=False, **fields):
    async def check_cancel():
        return cancelled

    async def invoke(name, arguments):
        raise AssertionError("This test never invokes a real external tool")

    return ExecutionContext(
        owner_id=uuid4(),
        run_id=state.run_id,
        config=state.run_metadata.config,
        brief_hash=state.brief_hash,
        lease_token=1,
        unit_id="plan:all",
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        cancel_check=check_cancel,
        invoke=invoke,
        emit=lambda event: None,
        **fields,
    )


def output(value, ctx):
    return PhaseResult(
        phase="plan",
        unit_id=ctx.unit_id,
        input_hash=value.semantic_hash,
        changes={"section_plans": plans()},
        degradations=[],
        failures=[],
    )


async def test_execute_phase_dispatches_copy_and_returns_result_without_advancing_or_io():
    state = initial_state()
    value = PhaseInput.from_state(state)
    ctx = context(state)

    async def worker(received, tools):
        assert received is not value and not hasattr(tools, "lease_token")
        assert not hasattr(tools, "owner_id") and not hasattr(tools, "repository")
        return output(received, ctx)

    result = await PhaseExecutor({"plan": worker}).execute_phase(value, ctx)
    assert result.phase == state.phase == "plan"
    assert state.section_plans == []


async def test_missing_worker_is_explicit_not_successful_empty_plan():
    state = initial_state()
    with pytest.raises(AppError, match="service_not_ready"):
        await PhaseExecutor({}).execute_phase(PhaseInput.from_state(state), context(state))


async def test_worker_mutating_slice_is_rejected_but_cannot_mutate_global_state():
    state = initial_state()
    value = PhaseInput.from_state(state)
    ctx = context(state)

    async def worker(received, tools):
        received.values.clear()
        return {"phase": "plan"}

    with pytest.raises(AppError, match="invalid_state"):
        await PhaseExecutor({"plan": worker}).execute_phase(value, ctx)
    assert set(value.values) == {"research_brief", "source_selection", "rework_targets"}


@pytest.mark.parametrize("altered", ["unit", "hash", "control"])
async def test_wrong_unit_input_hash_or_control_fields_are_rejected(altered):
    state = initial_state()
    ctx = context(state)

    async def worker(value, tools):
        result = output(value, ctx).model_dump()
        if altered == "unit":
            result["unit_id"] = "another-run-unit"
        elif altered == "hash":
            result["input_hash"] = "0" * 64
        else:
            result["changes"]["phase"] = "done"
        return result

    with pytest.raises(AppError, match="invalid_state"):
        await PhaseExecutor({"plan": worker}).execute_phase(PhaseInput.from_state(state), ctx)


async def test_cancel_or_elapsed_deadline_blocks_worker_before_tool_call():
    state = initial_state()
    calls = []

    async def worker(value, tools):
        calls.append(1)

    for ctx in (
        context(state, cancelled=True),
        ExecutionContext.model_validate(
            context(state).model_dump() | {"deadline": datetime.now(UTC) - timedelta(seconds=1)}
        ),
    ):
        with pytest.raises(AppError):
            await PhaseExecutor({"plan": worker}).execute_phase(PhaseInput.from_state(state), ctx)
    assert calls == []


async def test_frozen_brief_hash_or_source_policy_mismatch_blocks_worker_before_io():
    state = initial_state()
    value = PhaseInput.from_state(state)
    ctx = context(state)
    calls = []

    async def worker(value, tools):
        calls.append(1)

    wrong_hash = ExecutionContext.model_validate(ctx.model_dump() | {"brief_hash": "0" * 64})
    wrong_selection = PhaseInput.model_validate(
        value.model_dump()
        | {
            "values": value.values
            | {"source_selection": {"categories": ["papers"], "knowledge_base_ids": []}}
        }
    )
    for received, config in ((value, wrong_hash), (wrong_selection, ctx)):
        with pytest.raises(AppError, match="invalid_state"):
            await PhaseExecutor({"plan": worker}).execute_phase(received, config)
    assert calls == []


async def test_plan_worker_uses_scoped_tool_callback_and_shared_phase_result():
    import json

    from application.phase_workers import plan_worker

    state = initial_state()
    ctx = context(state)
    calls = []

    async def invoke(name, arguments):
        assert name == "llm" and arguments["phase"] == "plan"
        calls.append(arguments)
        return json.dumps({"section_plans": plans()})

    ctx = ExecutionContext.model_validate(ctx.model_dump() | {"invoke": invoke})
    result = await PhaseExecutor({"plan": plan_worker}).execute_phase(
        PhaseInput.from_state(state), ctx
    )
    assert len(result.changes["section_plans"]) == 5 and len(calls) == 1
    assert result.unit_id == ctx.unit_id and state.section_plans == []
