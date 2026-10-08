"""Early exhaustion preserves facts and declares incomplete work, never approval."""

import pytest

from domain.research.machine import contract_pipeline
from domain.research.phase_contracts import PhaseInput
from domain.research.state import PipelineState
from tests.unit.test_phase_contracts import plans
from tests.unit.test_state import initial_state


def test_mid_research_exhaustion_records_every_gap_without_fake_completed_units():
    initial = initial_state()
    state = PipelineState.model_validate(
        initial.model_dump()
        | {
            "phase": "research",
            "section_plans": plans(),
        }
    )
    before = state.model_dump_json()
    contracted, decision = contract_pipeline(state, "budget_exhausted")
    assert state.model_dump_json() == before
    assert contracted.phase == decision.next_phase == "write" and not decision.deliver
    assert contracted.final_report is None and contracted.review_verdict is None
    assert contracted.run_metadata.stop_reason == "budget_exhausted"
    assert contracted.run_metadata.config == state.run_metadata.config
    assert contracted.run_metadata.unit_manifest == state.run_metadata.unit_manifest
    assert contracted.run_id == state.run_id
    assert not contracted.evidence and not contracted.analysis_artifacts
    assert all(claim.claim_type == "hypothesis" for claim in contracted.claims.values())
    assert len(contracted.section_coverage) == 5
    assert all(item.gaps for item in contracted.section_coverage.values())
    PhaseInput.from_state(contracted)
    with pytest.raises(ValueError, match="already"):
        contract_pipeline(contracted, "budget_exhausted")


def test_plan_without_output_cannot_contract_to_a_fake_report():
    with pytest.raises(ValueError):
        contract_pipeline(initial_state(), "budget_exhausted")
