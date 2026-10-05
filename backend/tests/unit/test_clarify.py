"""Unit tests for architect.clarify."""

import pytest

from domain.ports import AdapterError
from domain.research.agents import architect
from domain.research.models import PartialResearchBrief, SourceSelection
from infrastructure.fake import FakeLLM


@pytest.mark.asyncio
async def test_clarify_returns_judgment_without_status() -> None:
    llm = FakeLLM(
        response='{"missing_fields": ["decision_goal"], "questions": ["What is the goal?"], '
        '"brief_patch": {}, "assumptions": [], "field_reasons": {}}'
    )
    result = (
        await architect.clarify(
            llm,
            draft=PartialResearchBrief(),
            query="help",
            answer="",
            source_selection=SourceSelection(),
            pending_questions=[],
            history=[],
        )
    ).model_dump()
    assert result["missing_fields"] == ["decision_goal"]
    assert result["questions"] == ["What is the goal?"]
    assert result["brief_patch"] == {}
    assert result["assumptions"] == []
    assert "status" not in result


@pytest.mark.asyncio
async def test_clarify_rejects_dirty_llm_output() -> None:
    llm = FakeLLM(response="not json at all")
    with pytest.raises(AdapterError, match="model_output_invalid"):
        await architect.clarify(
            llm,
            draft=PartialResearchBrief(),
            query="help",
            answer="",
            source_selection=SourceSelection(),
            pending_questions=[],
            history=[],
        )
