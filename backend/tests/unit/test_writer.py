"""Unit tests for writer.write_report and revise_report."""

import pytest

from domain.research.agents import writer
from infrastructure.fake import FakeLLM


@pytest.mark.asyncio
async def test_write_report_produces_sections_and_bindings() -> None:
    llm = FakeLLM(
        response='{"content": "Section prose.", "bindings": [{"statement_id": "st-0", '
        '"claim_ids": ["c1"], "cited_evidence_ids": ["e1"], "artifact_ids": []}]}'
    )
    result = await writer.write_report(
        [{"section_id": "s1", "objective": "o"}],
        {"c1": {"claim_id": "c1", "text": "claim"}},
        {"e1": {"evidence_id": "e1"}},
        {},
        {},
        {"research_object": "A report"},
        llm,
    )
    assert result["draft_sections"]["s1"]["content"] == "Section prose."
    assert result["draft_claim_bindings"][0]["cited_evidence_ids"] == ["e1"]
    assert result["final_report"]["title"] == "A report"
    assert result["final_report"]["sections"] == result["draft_sections"]


@pytest.mark.asyncio
async def test_write_report_empty_llm_output() -> None:
    llm = FakeLLM(response="{}")
    result = await writer.write_report([{"section_id": "s1"}], {}, {}, {}, {}, {}, llm)
    assert result["draft_sections"]["s1"]["content"] == ""
    assert result["draft_claim_bindings"] == []


@pytest.mark.asyncio
async def test_revise_report_uses_feedback() -> None:
    llm = FakeLLM(
        response='{"content": "Revised prose.", "bindings": [{"statement_id": "st-0", '
        '"claim_ids": [], "cited_evidence_ids": ["e1"], "artifact_ids": []}]}'
    )
    result = await writer.revise_report(
        [{"section_id": "s1", "objective": "o"}],
        {},
        {"e1": {"evidence_id": "e1"}},
        {},
        {},
        {},
        {"s1": {"section_id": "s1", "content": "old prose"}},
        [{"target_id": "s1", "issue_type": "overclaim", "description": "too strong"}],
        llm,
    )
    assert result["draft_sections"]["s1"]["content"] == "Revised prose."


@pytest.mark.asyncio
async def test_revise_report_keeps_unaffected_sections() -> None:
    llm = FakeLLM(
        response='{"content": "Revised prose.", "bindings": []}'
    )
    result = await writer.revise_report(
        [{"section_id": "s1", "objective": "o"}, {"section_id": "s2", "objective": "o2"}],
        {},
        {},
        {},
        {},
        {},
        {"s1": {"section_id": "s1", "content": "old"}, "s2": {"section_id": "s2", "content": "keep"}},
        [{"target_id": "s1", "issue_type": "overclaim", "description": "too strong"}],
        llm,
    )
    assert result["draft_sections"]["s1"]["content"] == "Revised prose."
    assert result["draft_sections"]["s2"]["content"] == "keep"
