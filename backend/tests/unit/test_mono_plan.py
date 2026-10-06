"""Frozen-brief mono planner; legacy CLI behavior is not the target contract."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from application.errors import AppError
from domain.ports import AdapterError
from domain.research.agents import architect
from domain.research.ids import canonical_hash
from domain.research.models import ResearchBrief
from infrastructure.fake import FakeLLM
from tests.unit.test_phase_contracts import plans
from tests.unit.test_state import initial_state


async def test_frozen_plan_returns_five_typed_sections_and_task_specific_prompt():
    llm = FakeLLM(response=json.dumps({"section_plans": plans()}))
    result = await architect.plan(llm, initial_state().research_brief)
    assert [section.section_id for section in result] == [
        f"section_{index}" for index in range(1, 6)
    ]
    assert result[0].claim_specs[0].spec_id == "spec-1"


@pytest.mark.parametrize(
    "output",
    [
        "",
        "{}",
        "[]",
        json.dumps({"section_plans": []}),
        json.dumps({"section_plans": plans()[:4]}),
        json.dumps({"section_plans": plans(), "phase": "done"}),
        '{"section_plans":[],"section_plans":[]}',
    ],
)
async def test_empty_or_invalid_plan_never_becomes_success(output):
    with pytest.raises(AdapterError) as failure:
        await architect.plan(FakeLLM(response=output), initial_state().research_brief)
    assert failure.value.code == "model_output_invalid" and not failure.value.retryable


async def test_plan_rejects_unfrozen_brief_before_calling_model():
    with pytest.raises(ValidationError):
        await architect.plan(FakeLLM(response=""), {"task_type": "evaluation_design"})


async def test_plan_schema_repair_is_once_and_counted_in_total_attempts():
    class Model:
        calls = 0

        async def complete(self, prompt):
            self.calls += 1
            assert "evaluation_design" in prompt and "section_1" in prompt
            assert "are ONLY numerical analysis" in prompt
            if self.calls == 1:
                return '{"section_plans":[]}'
            assert "Repair" in prompt and "previous_output" in prompt
            return json.dumps({"section_plans": plans()})

    llm = Model()
    assert len(await architect.plan(llm, initial_state().research_brief)) == 5
    assert llm.calls == 2


async def test_planner_cancellation_propagates_without_retry():
    class Model:
        calls = 0

        async def complete(self, prompt):
            self.calls += 1
            raise asyncio.CancelledError

    llm = Model()
    with pytest.raises(asyncio.CancelledError):
        await architect.plan(llm, initial_state().research_brief)
    assert llm.calls == 1


@pytest.mark.parametrize("code", ["budget_exhausted", "stale_resource", "invalid_session_state"])
async def test_trusted_tool_control_error_propagates_without_repair_or_retry(code):
    failure = AppError(code, "Controlled stop")

    class Model:
        calls = 0

        async def complete(self, prompt):
            self.calls += 1
            raise failure

    model = Model()
    with pytest.raises(AppError) as caught:
        await architect.plan(model, initial_state().research_brief)
    assert caught.value is failure and model.calls == 1


def test_recorded_real_plan_fixture_has_verified_hashes_and_no_prose_numeric_requirements():
    """Static artifact regression, not another model call or real E2E run."""
    path = (
        Path(__file__).resolve().parents[3]
        / "specs/004-backend-mono-alignment/evidence/t024-plan-real.json"
    )
    data = json.loads(path.read_text())
    parsed = architect.PlanOutput.model_validate({"section_plans": data["section_plans"]})
    assert canonical_hash(ResearchBrief.model_validate(data["brief"])) == data["brief_hash"]
    assert canonical_hash(data["section_plans"]) == data["plan_hash"]
    assert not any(section.analysis_requirements for section in parsed.section_plans)
