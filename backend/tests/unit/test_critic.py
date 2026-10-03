"""Unit tests for critic.review."""

from domain.research.agents.critic import review


def test_review_flags_missing_source() -> None:
    bindings = [{"statement_id": "st-0", "cited_evidence_ids": ["missing"]}]
    evidence = {"e1": {"evidence_id": "e1", "source_id": "s1"}}
    feedback = review(bindings, evidence)
    assert feedback[0]["issue_type"] == "missing_source"
    assert "required_action" not in feedback[0]


def test_review_no_feedback_when_all_evidence_known() -> None:
    bindings = [{"statement_id": "st-0", "cited_evidence_ids": ["e1"]}]
    evidence = {"e1": {"evidence_id": "e1", "source_id": "s1"}}
    assert review(bindings, evidence) == []
