"""Strict worker output and deterministic ten-field clarification decisions."""

import json

import pytest

from domain.ports import AdapterError
from domain.research.agents import architect
from domain.research.machine import assess_brief
from domain.research.models import ClarifyAssessment, PartialResearchBrief, SourceSelection
from infrastructure.fake import FakeLLM


def assessment(**updates):
    return {
        "missing_fields": [],
        "questions": [],
        "brief_patch": {},
        "assumptions": [],
        "field_reasons": {},
    } | updates


def core():
    return {
        "task_type": "evaluation_design",
        "decision_goal": "Design a reproducible evaluation",
        "research_object": "An intrusion detector",
        "deliverable": "An evaluation protocol",
    }


async def invoke(payload):
    return await architect.clarify(
        FakeLLM(response=payload),
        draft=PartialResearchBrief(),
        query="Design a detector evaluation",
        answer="",
        source_selection=SourceSelection(),
        pending_questions=[],
        history=[],
    )


async def test_assessment_is_typed_and_cannot_choose_status():
    result = await invoke(json.dumps(assessment(brief_patch=core())))
    assert isinstance(result, ClarifyAssessment)
    assert result.brief_patch.task_type == "evaluation_design"


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "{}",
        "[]",
        '{"missing_fields":[],"missing_fields":["scope"]}',
        json.dumps(assessment(status="ready")),
        json.dumps(assessment(questions=["one", "two", "three"])),
        json.dumps(assessment(brief_patch={"scope": " "})),
        json.dumps(assessment(brief_patch={"task_type": "研究设计"})),
        json.dumps(assessment(brief_patch={"task_type": "reviewer_response"})),
        json.dumps(assessment(missing_fields=["query"])),
        json.dumps(assessment(field_reasons={"owner_id": "override"})),
    ],
)
async def test_invalid_output_is_not_an_empty_success(payload):
    with pytest.raises(AdapterError) as failure:
        await invoke(payload)
    assert failure.value.code == "model_output_invalid"
    assert "not json" not in failure.value.message


def test_empty_patch_cannot_confirm_even_if_model_says_complete():
    decision = assess_brief(PartialResearchBrief(), ClarifyAssessment(**assessment()))
    assert decision.status == "ask"
    assert set(decision.missing_fields) == {
        "task_type",
        "decision_goal",
        "research_object",
        "deliverable",
    }
    assert 1 <= len(decision.questions) <= 2


def test_repeated_multiline_assumptions_are_not_appended_again():
    decision = assess_brief(
        PartialResearchBrief(**core(), assumptions="Public fixture.\nNo measured results."),
        ClarifyAssessment(**assessment(assumptions=["Public fixture.", "No measured results."])),
    )
    assert decision.draft.assumptions.count("Public fixture.") == 1
    assert decision.draft.assumptions.count("No measured results.") == 1


def test_safe_defaults_are_full_and_disclosed_but_do_not_freeze():
    decision = assess_brief(
        PartialResearchBrief(), ClarifyAssessment(**assessment(brief_patch=core()))
    )
    assert decision.status == "confirm"
    assert not decision.missing_fields and not decision.questions
    assert set(decision.draft.model_dump()) == {
        "task_type",
        "decision_goal",
        "research_object",
        "scope",
        "comparison_scope",
        "claims_to_verify",
        "evidence_requirements",
        "conclusion_boundary",
        "deliverable",
        "assumptions",
    }
    assert "scope" in decision.draft.assumptions
    assert "conclusion_boundary" in decision.draft.assumptions
    assert decision.status != "ready"


def test_semantic_gap_is_not_overridden_by_nonempty_field_or_default():
    decision = assess_brief(
        PartialResearchBrief(**core()),
        ClarifyAssessment(
            **assessment(
                brief_patch={"scope": "public benchmark"},
                field_reasons={"scope": "Dataset choice changes the evaluation decision"},
            )
        ),
    )
    assert decision.status == "ask"
    assert "scope" in decision.missing_fields
    assert decision.draft.scope == "public benchmark"


def test_model_assumptions_are_preserved_in_brief():
    decision = assess_brief(
        PartialResearchBrief(**core()),
        ClarifyAssessment(**assessment(assumptions=["No access to production data"])),
    )
    assert "No access to production data" in decision.draft.assumptions


async def test_llm_failure_is_a_dependency_error_not_blank_assessment():
    class Failing:
        async def complete(self, prompt):
            raise OSError("secret-upstream-address")

    with pytest.raises(AdapterError) as failure:
        await architect.clarify(
            Failing(),
            draft=PartialResearchBrief(),
            query="question",
            answer="",
            source_selection=SourceSelection(),
            pending_questions=[],
            history=[],
        )
    assert failure.value.code == "dependency_unavailable"
    assert "secret" not in str(failure.value)
