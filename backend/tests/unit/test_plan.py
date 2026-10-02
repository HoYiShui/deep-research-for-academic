"""Unit tests for architect.plan."""

import pytest

from domain.research.agents import architect
from infrastructure.fake import FakeLLM


@pytest.mark.asyncio
async def test_plan_returns_section_plans() -> None:
    llm = FakeLLM(response='{"section_plans": [{"section_id": "s1", "objective": "o"}]}')
    plans = await architect.plan(llm, {"task_type": "idea_exploration"})
    assert plans and plans[0]["section_id"] == "s1"


@pytest.mark.asyncio
async def test_plan_handles_empty_llm_output() -> None:
    llm = FakeLLM(response="")
    plans = await architect.plan(llm, {})
    assert plans == []
