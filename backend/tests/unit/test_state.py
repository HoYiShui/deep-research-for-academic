"""Unit tests for the frozen PipelineState contract (T043)."""

from domain.research.machine import WORKERS
from domain.research.state import (
    Claim,
    ClaimEvidenceLink,
    CriticFeedback,
    Evidence,
    PipelineState,
    QuantitativeObservation,
)


def test_pipeline_state_is_id_keyed() -> None:
    state = PipelineState()
    assert state.research_brief == {}
    for field in ("sources", "evidence", "claims", "comparable_metrics", "analysis_artifacts",
                  "draft_sections", "quantitative_observations", "section_coverage"):
        assert isinstance(getattr(state, field), dict)
    for field in ("section_plans", "claim_evidence_links", "draft_claim_bindings", "critic_feedback"):
        assert isinstance(getattr(state, field), list)


def test_evidence_contract_fields() -> None:
    ev = Evidence(evidence_id="e1", source_id="s1", location="p.7 Table 4")
    assert ev.evidence_id == "e1"
    assert ev.source_id == "s1"
    assert ev.quote_or_raw_content == ""


def test_critic_feedback_has_no_required_action() -> None:
    fb = CriticFeedback(issue_type="missing_source", severity="critical", fillable=True)
    assert not hasattr(fb, "required_action")
    assert fb.issue_type == "missing_source"


def test_workers_table_maps_each_phase() -> None:
    assert WORKERS == {
        "plan": "architect",
        "research": "scout",
        "analyze": "data_analyst",
        "write": "writer",
        "review": "critic",
    }


def test_claim_and_observation_contracts() -> None:
    claim = Claim(claim_id="c1", status="open")
    obs = QuantitativeObservation(observation_id="o1", evidence_id="e1")
    link = ClaimEvidenceLink(claim_id="c1", evidence_id="e1", relation="supports")
    assert claim.status == "open"
    assert obs.evidence_id == "e1"
    assert link.relation == "supports"
