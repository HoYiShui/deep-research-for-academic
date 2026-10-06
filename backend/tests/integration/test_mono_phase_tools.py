"""Canonical workers through durable tools: real PG/MinIO, controlled model."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from application.errors import AppError
from application.phase_executor import ExecutionContext, PhaseExecutor
from application.phase_tools import ModelBinding, PhaseTools
from application.phase_workers import plan_worker
from domain.model_completion import ModelCompletion
from domain.ports import AdapterError
from domain.research.phase_contracts import PhaseInput
from tests.integration.test_mono_tool_cache import service, started
from tests.unit.test_phase_contracts import plans


class ControlledModel:
    def __init__(self, model, *, stop="end_turn", actual_model=None):
        self.model, self.stop, self.actual_model = model, stop, actual_model
        self.prompts = []

    async def complete_metered(self, prompt):
        self.prompts.append(prompt)
        return ModelCompletion(
            response_id="controlled-response",
            model=self.actual_model or self.model,
            text=json.dumps({"section_plans": plans()}),
            stop_reason=self.stop,
            input_tokens=41,
            output_tokens=29,
        )


async def setup(pg_database, object_cache, *, task="evaluation_design", **model_fields):
    pool, store, user, commit, claimed = await started(pg_database, task=task)
    versions = claimed.run.config_snapshot.versions
    model = ControlledModel(versions.llm_model, **model_fields)
    binding = ModelBinding(
        model, versions.llm_provider, versions.llm_model, versions.llm_revision, 1000
    )
    tools = PhaseTools(
        service(store, claimed, object_cache),
        binding,
        [],
        model_slots=asyncio.Semaphore(claimed.run.config_snapshot.concurrency.llm),
    )
    point = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
    value = PhaseInput.from_state(point.state)
    return pool, store, user, commit, claimed, model, binding, tools, value


async def test_canonical_plan_dispatch_is_metered_cached_and_does_not_advance_state(
    pg_database, object_cache
):
    pool, store, user, commit, claimed, model, _binding, tools, value = await setup(
        pg_database, object_cache
    )

    async def cancel():
        return False

    ctx = ExecutionContext(
        owner_id=user.user_id,
        run_id=claimed.run.run_id,
        config=claimed.run.config_snapshot,
        brief_hash=claimed.run.brief_hash,
        lease_token=claimed.run.lease_token,
        unit_id="plan:all",
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        cancel_check=cancel,
        invoke=tools.for_phase(value),
        emit=lambda event: None,
    )
    executor = PhaseExecutor({"plan": plan_worker})
    first = await executor.execute_phase(value, ctx)
    second = await executor.execute_phase(value, ctx)
    assert first == second and len(first.changes["section_plans"]) == 5
    assert len(model.prompts) == 1
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
    assert await pool.fetchval("SELECT status FROM tool_calls") == "succeeded"
    budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
    assert budget.used.tokens == 70 and budget.used.llm_calls == 1
    assert budget.pending.tokens == 0
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 1
    assert await pool.fetchval("SELECT count(*) FROM reports") == 0


async def test_per_prompt_reservation_keeps_measured_usage_and_refuses_oversize_before_io(
    pg_database, object_cache
):
    pool, store, _, _, claimed, model, binding, _, value = await setup(pg_database, object_cache)
    tools = PhaseTools(
        service(store, claimed, object_cache),
        replace(binding, output_token_limit=100),
        [],
        model_slots=asyncio.Semaphore(2),
    )
    callback = tools.for_phase(value)
    await callback("llm", {"phase": "plan", "prompt": "public fixture"})
    assert (
        await pool.fetchval("SELECT tokens_reserved FROM tool_call_attempts")
        == len(b"public fixture") + 64 + 100
    )
    assert await pool.fetchval("SELECT tokens_used FROM tool_call_attempts") == 70
    with pytest.raises(AppError, match="budget_exhausted"):
        await callback("llm", {"phase": "plan", "prompt": "oversize" * 1000})
    assert len(model.prompts) == 1
    assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"terminal": True},
        {"allow_uncertain_replay": True},
        {"token_reservation": 1},
        {"phase": "write"},
    ],
)
async def test_worker_cannot_override_tool_authority(pg_database, object_cache, extra):
    pool, _store, _user, _commit, _claimed, model, _binding, tools, value = await setup(
        pg_database, object_cache
    )
    callback = tools.for_phase(value)
    with pytest.raises(AppError, match="invalid_state"):
        await callback("llm", {"phase": "plan", "prompt": "public fixture"} | extra)
    assert model.prompts == []
    assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 0


@pytest.mark.parametrize("change", ["provider", "model", "revision"])
async def test_wrong_model_binding_is_rejected_before_io(pg_database, object_cache, change):
    pool, store, _user, _commit, claimed, model, binding, _tools, _value = await setup(
        pg_database, object_cache
    )
    with pytest.raises(AppError, match="invalid_state"):
        PhaseTools(
            service(store, claimed, object_cache),
            replace(binding, **{change: "other"}),
            [],
            model_slots=asyncio.Semaphore(2),
        )
    assert model.prompts == []
    assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 0


@pytest.mark.parametrize("fields", [{"stop": "max_tokens"}, {"actual_model": "wrong-model"}])
async def test_unusable_model_response_keeps_measured_usage_and_cached_result(
    pg_database, object_cache, fields
):
    pool, store, user, commit, _claimed, model, _binding, tools, value = await setup(
        pg_database, object_cache, **fields
    )
    callback = tools.for_phase(value)
    for _ in range(2):
        with pytest.raises(AdapterError, match="model_output_invalid"):
            await callback("llm", {"phase": "plan", "prompt": "public fixture"})
    assert len(model.prompts) == 1
    assert await pool.fetchval("SELECT tokens_used FROM tool_call_attempts") == 70
    budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
    assert budget.used.tokens == 70 and budget.used.llm_calls == 1


async def test_changed_prompt_has_distinct_call_identity_and_spend(pg_database, object_cache):
    pool, store, user, commit, _claimed, model, _binding, tools, value = await setup(
        pg_database, object_cache
    )
    callback = tools.for_phase(value)
    for prompt in ["first public prompt", "second public prompt", "first public prompt"]:
        await callback("llm", {"phase": "plan", "prompt": prompt})
    assert len(model.prompts) == 2
    assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 2
    budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
    assert budget.used.tokens == 140 and budget.used.llm_calls == 2


async def test_unconfigured_tools_never_reach_legacy_or_fake_io(pg_database, object_cache):
    pool, _store, _user, _commit, _claimed, model, _binding, tools, value = await setup(
        pg_database, object_cache
    )
    with pytest.raises(AppError, match="service_not_ready"):
        await tools.for_phase(value)("search", {"query": "public fixture"})
    assert model.prompts == []
    assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 0


async def test_separate_phase_bindings_share_composition_root_model_slots(
    pg_database, object_cache
):
    pool, store, _user, _commit, claimed, model, binding, _tools, value = await setup(
        pg_database, object_cache
    )
    entered, release = asyncio.Event(), asyncio.Event()
    active, peak = 0, 0
    original = model.complete_metered

    async def controlled(prompt):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await release.wait()
            return await original(prompt)
        finally:
            active -= 1

    model.complete_metered = controlled
    slots = asyncio.Semaphore(1)
    callbacks = [
        PhaseTools(service(store, claimed, object_cache), binding, [], model_slots=slots).for_phase(
            value
        )
        for _ in range(2)
    ]
    first = asyncio.create_task(callbacks[0]("llm", {"phase": "plan", "prompt": "first"}))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        second = asyncio.create_task(callbacks[1]("llm", {"phase": "plan", "prompt": "second"}))
        await asyncio.sleep(0)  # Let second reach the shared semaphore, not a timed sleep.
        assert slots.locked() and peak == active == 1
        assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        assert peak == 1 and len(model.prompts) == 2
    finally:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)
