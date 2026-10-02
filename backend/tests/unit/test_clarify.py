"""Unit tests for architect.clarify."""

import pytest

from domain.research.agents import architect
from infrastructure.fake import FakeLLM


@pytest.mark.asyncio
async def test_clarify_returns_judgment_without_status() -> None:
    llm = FakeLLM(
        response='{"missing_fields": ["decision_goal"], "questions": ["What is the goal?"], '
        '"brief_patch": {}, "assumptions": []}'
    )
    result = await architect.clarify(llm, {}, "help")
    assert result["missing_fields"] == ["decision_goal"]
    assert result["questions"] == ["What is the goal?"]
    assert result["brief_patch"] == {}
    assert result["assumptions"] == []
    assert "status" not in result


@pytest.mark.asyncio
async def test_clarify_handles_dirty_llm_output() -> None:
    llm = FakeLLM(response="not json at all")
    result = await architect.clarify(llm, {}, "help")
    assert result["missing_fields"] == []
    assert result["questions"] == []
