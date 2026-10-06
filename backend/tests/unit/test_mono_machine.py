"""Formal control policy is not the legacy dict fallback routing table."""

import pytest

from application.phase_units import plan_units
from domain.research.machine import apply_pipeline_decision, decide_pipeline
from domain.research.phase_contracts import merge_phase_result
from domain.research.state import PipelineState
from tests.unit.test_phase_contracts import (
    drafts,
    issue,
    plans,
    result,
    writing_state,
    written_state,
)
from tests.unit.test_state import initial_state


def reviewed(**fields):
    state = written_state()
    return PipelineState.model_validate(
        state.model_dump()
        | {"reviewed_draft_version": 1, "review_verdict": "needs_more_work", **fields}
    )


def test_plan_requires_valid_output_and_advances_without_mutating_input():
    state = initial_state()
    with pytest.raises(ValueError):
        decide_pipeline(state)
    planned = merge_phase_result(state, result(state, {"section_plans": plans()}))
    decision = decide_pipeline(planned)
    newer = apply_pipeline_decision(planned, decision)
    assert newer.phase == "research" and planned.phase == "plan" and state.section_plans == []


def test_research_requires_complete_spec_coverage_or_explicit_gap():
    state = PipelineState.model_validate(writing_state().model_dump() | {"phase": "research"})
    assert decide_pipeline(state).next_phase == "analyze"
    data = state.model_dump()
    data["section_coverage"]["section_1"]["gaps"] = []
    with pytest.raises(ValueError, match="gap"):
        decide_pipeline(PipelineState.model_validate(data))


def test_no_quantitative_analysis_requires_explicit_skip_reason():
    state = PipelineState.model_validate(writing_state().model_dump() | {"phase": "analyze"})
    with pytest.raises(ValueError, match="skip reason"):
        decide_pipeline(state)


@pytest.mark.parametrize(
    "kind,fillable,phase,action",
    [
        ("missing_source", True, "research", "re_research"),
        ("missing_source", False, "write", "acknowledge_limit"),
        ("hallucination", False, "write", "acknowledge_limit"),
        ("hallucination", True, "research", "re_research"),
        ("comparability_violation", False, "analyze", "re_analyze"),
        ("overclaim", False, "write", "revise"),
        ("outdated", False, "write", "acknowledge_limit"),
        ("logic_error", True, "research", "re_research"),
    ],
)
def test_closed_review_policy_routes_explicit_targets(kind, fillable, phase, action):
    state = reviewed(critic_feedback=[issue() | {"issue_type": kind, "fillable": fillable}])
    decision = decide_pipeline(state)
    assert decision.next_phase == phase and not decision.deliver and decision.rework_count == 1
    assert decision.targets[0].action == action
    assert decision.targets[0].issue_ids == ["controlled-issue"]
    assert decision.targets[0].section_ids == ["section_1"]
    newer = apply_pipeline_decision(state, decision)
    assert newer.phase == phase and newer.review_verdict is None
    assert state.phase == "review" and state.review_verdict == "needs_more_work"


def test_multiple_issues_preserve_all_targets_and_pick_earliest_phase():
    state = reviewed(
        critic_feedback=[
            issue() | {"issue_id": "a", "issue_type": "overclaim"},
            issue() | {"issue_id": "b", "issue_type": "missing_source", "fillable": True},
        ]
    )
    decision = decide_pipeline(state)
    assert decision.next_phase == "research" and len(decision.targets) == 2


def test_delivery_candidate_never_changes_or_upgrades_verdict_and_cannot_set_done():
    state = reviewed(review_verdict="needs_more_work")
    decision = decide_pipeline(state)
    assert decision.deliver and decision.next_phase is None
    assert state.phase == "review" and state.review_verdict == "needs_more_work"
    with pytest.raises(ValueError):
        apply_pipeline_decision(state, decision)


def test_old_resolved_issue_requires_current_version_confirmation():
    state = reviewed(
        critic_feedback=[
            issue() | {"resolved": True, "resolution": "Verified", "resolved_in_version": None}
        ]
    )
    with pytest.raises(ValueError, match="current-version"):
        decide_pipeline(state)


def test_rework_limit_selects_only_one_terminal_contraction_not_approval():
    state = reviewed(critic_feedback=[issue() | {"issue_type": "overclaim"}])
    data = state.model_dump()
    data["run_metadata"]["rework_count"] = 3
    state = PipelineState.model_validate(data)
    decision = decide_pipeline(state)
    assert decision.next_phase == "write" and decision.stop_reason == "rework_limit"
    assert decision.rework_count == 3 and not decision.deliver
    data["run_metadata"]["stop_reason"] = "rework_limit"
    with pytest.raises(ValueError, match="unsafe"):
        decide_pipeline(PipelineState.model_validate(data))


def test_terminal_insufficiency_can_be_candidate_only_without_factual_assertions():
    state = reviewed(critic_feedback=[issue()])
    data = state.model_dump()
    data["run_metadata"]["stop_reason"] = "rework_limit"
    decision = decide_pipeline(PipelineState.model_validate(data))
    assert decision.deliver  # Serializer must still enforce full report gates.
    data["review_verdict"] = "approved"
    with pytest.raises(ValueError):
        decide_pipeline(PipelineState.model_validate(data))


def test_unknown_issue_is_schema_failure_not_default_done_or_revision():
    with pytest.raises(ValueError):
        reviewed(critic_feedback=[issue() | {"issue_type": "unknown"}])


def test_query_units_are_checkpointable_and_ids_survive_outputs_budget_changes():
    state = PipelineState.model_validate(writing_state().model_dump() | {"phase": "research"})
    units = plan_units(state)
    assert len(units) == 10
    assert [unit.parameters["kind"] for unit in units] == ["query", "coverage"] * 5
    data = state.model_dump()
    data["run_metadata"]["budget_used"]["tokens"] = 7
    assert plan_units(PipelineState.model_validate(data)) == units
    data["run_metadata"]["rework_count"] = 1
    assert {unit.unit_id for unit in plan_units(PipelineState.model_validate(data))}.isdisjoint(
        {unit.unit_id for unit in units}
    )


def test_write_unit_identity_survives_draft_version_change():
    state = writing_state()
    first = plan_units(state)
    written = merge_phase_result(
        state,
        result(
            state, {"draft_version": 1, "draft_sections": drafts(1), "draft_claim_bindings": []}
        ),
    )
    later = PipelineState.model_validate(written.model_dump() | {"phase": "write"})
    assert plan_units(later) == first


def test_terminal_unit_generation_is_distinct_and_forbids_retrieval_or_analysis():
    state = writing_state()
    units = plan_units(state)
    data = state.model_dump()
    data["run_metadata"]["stop_reason"] = "rework_limit"
    assert plan_units(PipelineState.model_validate(data)) != units
    for phase in ["research", "analyze"]:
        with pytest.raises(ValueError):
            plan_units(PipelineState.model_validate(data | {"phase": phase}))
