"""Canonical worker slices and output authority, independent of storage/LLM."""

import pytest
from pydantic import ValidationError

from domain.research.phase_contracts import PhaseInput, PhaseResult, merge_phase_result
from domain.research.state import PipelineState
from tests.unit.test_state import initial_state, populated_data


def plans():
    return [
        {
            "section_id": f"section_{index}",
            "title": "Explicit research section",
            "objective": "Test phase contracts, not research quality",
            "claim_specs": [
                {
                    "spec_id": f"spec-{index}",
                    "text": "Bounded hypothesis",
                    "required_conditions": [],
                    "required_source_tiers": [],
                }
            ],
            "sub_questions": ["What original evidence supports this hypothesis?"],
            "retrieval_anchors": [],
            "evidence_requirements": [],
            "analysis_requirements": [],
        }
        for index in range(1, 6)
    ]


def test_research_units_prefer_deduplicated_anchors_but_preserve_question_obligations():
    from application.phase_units import plan_units

    data = initial_state().model_dump(mode="json") | {"phase": "research", "section_plans": plans()}
    section = data["section_plans"][0]
    section["retrieval_anchors"] = ["arXiv:1706.03762v7", "arXiv:1706.03762v7", "Transformer BLEU"]
    data["section_plans"][1]["sub_questions"] = []
    data["section_plans"][1]["retrieval_anchors"] = ["Advice background only"]
    state = PipelineState.model_validate(data)
    units = plan_units(state)
    queries = [unit for unit in units if unit.parameters["kind"] == "query"]
    assert [unit.parameters["query"] for unit in queries[:2]] == [
        "arXiv:1706.03762v7",
        "Transformer BLEU",
    ]
    assert not any(unit.section_ids == ["section_2"] for unit in queries)
    assert queries[2].parameters["query"] == plans()[2]["sub_questions"][0]
    assert state.section_plans[0].sub_questions == section["sub_questions"]
    assert units == plan_units(PipelineState.model_validate_json(state.model_dump_json()))


def result(state, changes, **extra):
    value = PhaseInput.from_state(state)
    return PhaseResult(
        phase=state.phase,
        unit_id="controlled-unit",
        input_hash=value.semantic_hash,
        changes=changes,
        degradations=[],
        failures=[],
        **extra,
    )


def test_plan_worker_receives_only_brief_sources_rework_and_no_global_state():
    state = initial_state()
    value = PhaseInput.from_state(state)
    assert set(value.values) == {"research_brief", "source_selection", "rework_targets"}
    assert "run_metadata" not in value.values and "session_id" not in value.values
    with pytest.raises(ValidationError):
        PhaseInput.model_validate(value.model_dump() | {"values": value.values | {"phase": "done"}})


def test_slice_hash_is_stable_across_budget_and_timestamp_changes():
    state = initial_state()
    updated = state.model_dump(mode="json")
    updated["run_metadata"]["budget_used"]["llm_calls"] = 1
    updated["run_metadata"]["budget_used"]["elapsed_s"] = 5
    assert (
        PhaseInput.from_state(state).semantic_hash
        == PhaseInput.from_state(PipelineState.model_validate(updated)).semantic_hash
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("phase", "done"),
        ("final_report", None),
        ("run_metadata", {}),
        ("research_brief", {}),
        ("lease_token", 2),
        ("session_status", "completed"),
    ],
)
def test_worker_cannot_return_control_plane_fields(key, value):
    with pytest.raises(ValidationError):
        result(initial_state(), {key: value})


def test_plan_merges_without_advancing_phase_and_preserves_frozen_input():
    state = initial_state()
    merged = merge_phase_result(state, result(state, {"section_plans": plans()}))
    assert len(merged.section_plans) == 5 and merged.phase == "plan"
    assert merged.research_brief == state.research_brief and merged.brief_hash == state.brief_hash
    assert state.section_plans == [] and merged.run_metadata == state.run_metadata


@pytest.mark.parametrize("bad", [[], plans()[:4], plans() + plans()[:1]])
def test_empty_incomplete_and_duplicate_plans_fail_without_empty_success(bad):
    state = initial_state()
    with pytest.raises(ValueError):
        merge_phase_result(state, result(state, {"section_plans": bad}))


def test_plan_requires_real_work_but_advice_chapter_can_reuse_a_shared_spec():
    state = initial_state()
    bad = plans()
    for section in bad:
        section["claim_specs"] = []
    with pytest.raises(ValueError):
        merge_phase_result(state, result(state, {"section_plans": bad}))
    bad = plans()
    bad[1]["claim_specs"][0]["spec_id"] = "spec-1"
    bad[1]["claim_specs"][0]["text"] = "Conflicting different requirement"
    with pytest.raises(ValueError):
        merge_phase_result(state, result(state, {"section_plans": bad}))
    shared = plans()
    shared[4]["claim_specs"] = shared[0]["claim_specs"]
    shared[4]["sub_questions"] = []
    assert (
        len(merge_phase_result(state, result(state, {"section_plans": shared})).section_plans) == 5
    )


def test_stale_semantic_input_and_wrong_phase_rejected():
    state = initial_state()
    value = result(state, {"section_plans": plans()})
    old = PhaseResult.model_validate(value.model_dump() | {"input_hash": "0" * 64})
    with pytest.raises(ValueError):
        merge_phase_result(state, old)
    with pytest.raises(ValidationError):
        PhaseResult.model_validate(value.model_dump() | {"phase": "research"})


@pytest.mark.parametrize("phase", ["write", "review"])
def test_phase_semantic_preconditions_reject_empty_evidence_or_draft(phase):
    state = PipelineState.model_validate(
        initial_state().model_dump() | {"phase": phase, "section_plans": plans()}
    )
    with pytest.raises(ValueError):
        PhaseInput.from_state(state)


def test_phase_result_primitive_fields_are_strict_and_require_all_contract_keys():
    state = initial_state()
    base = result(state, {"section_plans": plans()}).model_dump()
    with pytest.raises(ValidationError):
        PhaseResult.model_validate(base | {"phase": "write", "changes": {"draft_version": "1"}})
    with pytest.raises(ValidationError):
        PhaseResult.model_validate({key: value for key, value in base.items() if key != "failures"})


def writing_state():
    return PipelineState.model_validate(
        initial_state().model_dump()
        | {
            "phase": "write",
            "section_plans": plans(),
            "section_coverage": {
                f"section_{index}": {
                    "section_id": f"section_{index}",
                    "claim_spec_ids": [f"spec-{index}"],
                    "claim_ids": [],
                    "evidence_ids": [],
                    "covered_claim_ids": [],
                    "gaps": [
                        {
                            "gap_id": f"gap-{index}",
                            "section_id": f"section_{index}",
                            "claim_spec_id": f"spec-{index}",
                            "claim_id": None,
                            "reason": "Original evidence unavailable",
                            "fillable": False,
                            "verification_action": "Do not assert the hypothesis",
                        }
                    ],
                    "unresolved_items": [],
                }
                for index in range(1, 6)
            },
        }
    )


def drafts(version):
    return {
        f"section_{index}": {
            "section_id": f"section_{index}",
            "title": "Controlled insufficiency",
            "content": "Evidence is insufficient; no supported research finding.",
            "draft_version": version,
            "statements": [
                {
                    "statement_id": f"statement-{index}",
                    "text": "Evidence is insufficient",
                    "kind": "limitation",
                }
            ],
            "task_payload": None,
        }
        for index in range(1, 6)
    }


def written_state():
    state = writing_state()
    written = merge_phase_result(
        state,
        result(
            state, {"draft_version": 1, "draft_sections": drafts(1), "draft_claim_bindings": []}
        ),
    )
    return PipelineState.model_validate(written.model_dump() | {"phase": "review"})


def test_write_advances_only_global_draft_version_not_phase_and_clears_stale_review():
    old = written_state()
    state = PipelineState.model_validate(
        old.model_dump()
        | {"phase": "write", "reviewed_draft_version": 1, "review_verdict": "needs_more_work"}
    )
    replacement = drafts(2)["section_1"] | {"content": "Explicit revised limitation"}
    changed = merge_phase_result(
        state,
        result(
            state,
            {
                "draft_version": 2,
                "draft_sections": {"section_1": replacement},
                "draft_claim_bindings": [],
            },
        ),
        target_sections={"section_1"},
    )
    assert changed.phase == "write" and changed.draft_version == 2
    assert changed.draft_sections["section_2"].content == state.draft_sections["section_2"].content
    assert all(item.draft_version == 2 for item in changed.draft_sections.values())
    assert changed.reviewed_draft_version is None and changed.review_verdict is None
    assert state.draft_version == 1


def test_write_cannot_escape_target_chapter_or_skip_global_version():
    state = writing_state()
    with pytest.raises(ValueError):
        merge_phase_result(
            state,
            result(
                state, {"draft_version": 1, "draft_sections": drafts(1), "draft_claim_bindings": []}
            ),
            target_sections={"section_1"},
        )
    with pytest.raises(ValueError):
        merge_phase_result(
            state,
            result(
                state, {"draft_version": 2, "draft_sections": drafts(2), "draft_claim_bindings": []}
            ),
        )


def test_review_judges_current_version_without_changing_draft_or_phase():
    state = written_state()
    reviewed = merge_phase_result(
        state,
        result(
            state,
            {
                "critic_feedback": [],
                "reviewed_draft_version": 1,
                "review_verdict": "needs_more_work",
            },
        ),
    )
    assert reviewed.reviewed_draft_version == 1 and reviewed.phase == "review"
    assert reviewed.draft_sections == state.draft_sections and reviewed.final_report is None
    with pytest.raises(ValueError):
        merge_phase_result(
            state,
            result(
                state,
                {"critic_feedback": [], "reviewed_draft_version": 2, "review_verdict": "approved"},
            ),
        )


def issue():
    return {
        "issue_id": "controlled-issue",
        "draft_version": 1,
        "target_type": "statement",
        "target_id": "statement-1",
        "section_id": "section_1",
        "issue_type": "missing_source",
        "severity": "major",
        "fillable": False,
        "description": "Controlled test issue",
        "resolved": False,
        "resolution": None,
        "resolved_in_version": None,
    }


def test_review_preserves_feedback_identity_and_requires_verified_resolution():
    state = written_state()
    state = merge_phase_result(
        state,
        result(
            state,
            {
                "critic_feedback": [issue()],
                "reviewed_draft_version": 1,
                "review_verdict": "needs_more_work",
            },
        ),
    )
    for altered in (
        {"description": "silently changed"},
        {"resolved": True},
        {"resolved": True, "resolution": "fixed", "resolved_in_version": 2},
    ):
        with pytest.raises(ValueError):
            merge_phase_result(
                state,
                result(
                    state,
                    {
                        "critic_feedback": [issue() | altered],
                        "reviewed_draft_version": 1,
                        "review_verdict": "approved",
                    },
                ),
            )
    resolved = issue() | {
        "resolved": True,
        "resolution": "Explicit limit verified",
        "resolved_in_version": 1,
    }
    changed = merge_phase_result(
        state,
        result(
            state,
            {
                "critic_feedback": [resolved],
                "reviewed_draft_version": 1,
                "review_verdict": "approved_with_risks",
            },
        ),
    )
    assert changed.critic_feedback[0].resolved


def test_research_merges_target_coverage_and_rejects_dangling_references():
    state = PipelineState.model_validate(
        writing_state().model_dump() | {"phase": "research", "section_coverage": {}}
    )
    coverage = writing_state().section_coverage["section_1"]
    changed = merge_phase_result(
        state,
        result(state, {"section_coverage": {"section_1": coverage}}),
        target_sections={"section_1"},
    )
    assert set(changed.section_coverage) == {"section_1"}
    assert changed.phase == "research" and state.section_coverage == {}
    with pytest.raises(ValueError):
        merge_phase_result(
            state,
            result(state, {"section_coverage": {"section_1": coverage}}),
            target_sections={"section_2"},
        )
    bad = coverage.model_dump() | {"evidence_ids": ["not-present"]}
    with pytest.raises(ValueError):
        merge_phase_result(state, result(state, {"section_coverage": {"section_1": bad}}))


def test_analyze_without_requirements_can_skip_but_cannot_fabricate_derived_scope():
    state = PipelineState.model_validate(writing_state().model_dump() | {"phase": "analyze"})
    assert merge_phase_result(state, result(state, {})) == state
    with pytest.raises(ValueError):
        merge_phase_result(
            state,
            result(
                state, {"comparable_metrics": {}, "comparison_sets": {}, "analysis_artifacts": {}}
            ),
        )


def analyzed_state():
    data = populated_data()
    data["claims"]["c1"]["spec_ids"] = ["spec-1"]
    data.update(
        phase="analyze",
        section_plans=plans(),
        draft_sections=drafts(1),
        draft_version=1,
        reviewed_draft_version=1,
        review_verdict="needs_more_work",
    )
    for index in (1, 2):
        data["section_plans"][index - 1]["analysis_requirements"] = [
            {
                "requirement_id": f"req-{index}",
                "operation": "statistic",
                "claim_spec_ids": [f"spec-{index}"],
                "required_context_fields": ["dataset"],
                "parameters": {"kind": "mean", "group_by": []},
            }
        ]
    data["quantitative_observations"] = {
        "o1": {
            "observation_id": "o1",
            "evidence_id": "e1",
            "kind": "benchmark_result",
            "row_key": {},
            "column_key": {"metric": "fixture"},
            "raw_value": "1",
            "value": "1",
            "uncertainty": None,
            "statistic": "mean",
            "context": {"dataset": "fixture"},
            "unit": "%",
        }
    }
    data["comparable_metrics"] = {
        "m1": {
            "comparable_metric_id": "m1",
            "observation_ids": ["o1"],
            "metric_definition": "Controlled fixture metric",
            "evaluated_method": "Fixture A",
            "evaluation_context": {"dataset": "fixture"},
            "value": "1",
            "unit": "%",
            "normalization_basis": "Raw fixture",
            "missing_context_fields": [],
        }
    }
    data["comparison_sets"] = {
        f"g{index}": {
            "comparison_set_id": f"g{index}",
            "section_id": f"section_{index}",
            "requirement_id": f"req-{index}",
            "metric_ids": ["m1"],
            "required_context_fields": ["dataset"],
            "comparability": "compatible",
            "reasons": ["Controlled matching fixture"],
        }
        for index in (1, 2)
    }
    return PipelineState.model_validate(data)


def analysis_changes(state):
    return {
        "comparable_metrics": {
            "m3": state.comparable_metrics["m1"].model_dump() | {"comparable_metric_id": "m3"}
        },
        "comparison_sets": {
            "g3": state.comparison_sets["g1"].model_dump()
            | {"comparison_set_id": "g3", "metric_ids": ["m3"]}
        },
        "analysis_artifacts": {},
    }


def test_analysis_replaces_target_requirement_preserves_shared_metric_and_invalidates_only_target_draft():
    state = analyzed_state()
    changed = merge_phase_result(
        state,
        result(state, analysis_changes(state)),
        target_sections={"section_1"},
        target_requirements={"req-1"},
    )
    assert set(changed.comparison_sets) == {"g2", "g3"}
    assert changed.comparison_sets["g2"] == state.comparison_sets["g2"]
    assert set(changed.comparable_metrics) == {"m1", "m3"}
    assert "section_1" not in changed.draft_sections and "section_2" in changed.draft_sections
    assert changed.review_verdict is None and changed.reviewed_draft_version is None
    assert changed.run_metadata.rework_targets[-1].section_ids == ["section_1"]
    assert changed.run_metadata.budget_used == state.run_metadata.budget_used
    assert "section_1" in state.draft_sections


def test_analysis_cannot_replace_unrelated_requirement_or_shared_metric_identity():
    state = analyzed_state()
    changes = analysis_changes(state)
    with pytest.raises(ValueError):
        merge_phase_result(
            state,
            result(state, changes),
            target_sections={"section_2"},
            target_requirements={"req-2"},
        )
    changes["comparison_sets"]["g3"]["metric_ids"] = ["m1"]
    changes["comparable_metrics"] = {
        "m1": state.comparable_metrics["m1"].model_dump() | {"value": "2"}
    }
    with pytest.raises(ValueError):
        merge_phase_result(
            state,
            result(state, changes),
            target_sections={"section_1"},
            target_requirements={"req-1"},
        )


def test_research_same_id_different_quote_or_hash_rejected_without_mutating_original():
    base = populated_data()
    base.update(phase="research", section_plans=plans())
    base["claims"]["c1"]["spec_ids"] = ["spec-1"]
    state = PipelineState.model_validate(base)
    for changed in (
        {"quote_or_raw_content": "Contradicting replacement"},
        {"content_hash": "b" * 64},
    ):
        with pytest.raises(ValueError):
            merge_phase_result(
                state,
                result(state, {"evidence": {"e1": state.evidence["e1"].model_dump() | changed}}),
            )
    assert state.evidence["e1"].quote_or_raw_content == "Controlled fixture excerpt"


def test_review_new_issue_must_target_an_actual_current_object():
    state = written_state()
    with pytest.raises(ValueError):
        merge_phase_result(
            state,
            result(
                state,
                {
                    "critic_feedback": [issue() | {"target_id": "invented-object"}],
                    "reviewed_draft_version": 1,
                    "review_verdict": "needs_more_work",
                },
            ),
        )
