"""Unit tests for critic.review."""

import pytest

from domain.research.agents import critic
from infrastructure.fake import FakeLLM


@pytest.mark.asyncio
async def test_review_flags_missing_source() -> None:
    bindings = [{"statement_id": "st-0", "cited_evidence_ids": ["missing"]}]
    evidence = {"e1": {"evidence_id": "e1", "source_id": "s1"}}
    feedback = await critic.review(bindings, {}, evidence, {}, FakeLLM())
    assert feedback[0]["issue_type"] == "missing_source"
    assert feedback[0]["fillable"] is True
    assert "required_action" not in feedback[0]


@pytest.mark.asyncio
async def test_review_no_feedback_when_all_evidence_known() -> None:
    bindings = [{"statement_id": "st-0", "cited_evidence_ids": ["e1"]}]
    evidence = {"e1": {"evidence_id": "e1", "source_id": "s1"}}
    assert await critic.review(bindings, {}, evidence, {}, FakeLLM()) == []


@pytest.mark.asyncio
async def test_review_llm_flags_overclaim() -> None:
    llm = FakeLLM(
        response='{"issues": [{"target_type": "draft_section", "target_id": "s1", '
        '"issue_type": "overclaim", "severity": "major", "description": "too strong"}]}'
    )
    feedback = await critic.review(
        [{"statement_id": "st-0", "cited_evidence_ids": ["e1"]}], {}, {"e1": {}}, {}, llm
    )
    assert any(f["issue_type"] == "overclaim" for f in feedback)
    assert all("required_action" not in f for f in feedback)
