"""Full mono-v1 checkpoint identity and serialization boundaries."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from application.settings import Settings
from domain.research.ids import canonical_hash
from domain.research.state import Checkpoint, PipelineState


def initial_state():
    config = Settings().run_config_snapshot()
    return PipelineState.initial(
        session_id=uuid4(),
        run_id=uuid4(),
        brief_version=1,
        research_brief={
            "task_type": "evaluation_design",
            "assumptions": "",
            **{
                key: "Public scope and explicit decision boundary"
                for key in (
                    "decision_goal",
                    "research_object",
                    "scope",
                    "comparison_scope",
                    "claims_to_verify",
                    "evidence_requirements",
                    "conclusion_boundary",
                    "deliverable",
                )
            },
        },
        source_selection={},
        config=config,
    )


def test_initial_state_has_complete_top_level_and_json_round_trip():
    state = initial_state()
    assert state.phase == "plan"
    assert state.final_report is None
    assert state.draft_version == 0
    assert state.comparison_sets == {}
    assert state.run_metadata.budget_used.llm_calls == 0
    data = state.model_dump(mode="json")
    assert set(data) == {
        "schema_version",
        "session_id",
        "run_id",
        "brief_version",
        "brief_hash",
        "phase",
        "research_brief",
        "source_selection",
        "section_plans",
        "sources",
        "evidence",
        "claims",
        "claim_evidence_links",
        "quantitative_observations",
        "comparable_metrics",
        "comparison_sets",
        "analysis_artifacts",
        "section_coverage",
        "draft_sections",
        "draft_claim_bindings",
        "critic_feedback",
        "draft_version",
        "reviewed_draft_version",
        "review_verdict",
        "final_report",
        "run_metadata",
        "errors",
    }
    assert PipelineState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError):
        PipelineState.model_validate(data | {"phase": "completed"})
    with pytest.raises(ValidationError):
        PipelineState.model_validate(data | {"brief_hash": "0" * 64})
    with pytest.raises(ValidationError):
        PipelineState.model_validate(data | {"free_form": "unsupported"})


def test_persisted_state_requires_complete_keys_not_just_minimal_cli_input():
    data = initial_state().model_dump(mode="json")
    for key in data:
        missing = data.copy()
        del missing[key]
        with pytest.raises(ValidationError):
            PipelineState.model_validate(missing)


def test_checkpoint_checks_state_hash_run_identity_and_next_phase():
    state = initial_state()
    data = {
        "snapshot_id": uuid4(),
        "run_id": state.run_id,
        "seq": 1,
        "schema_version": 1,
        "phase": state.phase,
        "state": state,
        "state_hash": canonical_hash(state),
        "created_at": datetime.now(UTC),
    }
    checkpoint = Checkpoint(**data)
    assert Checkpoint.model_validate_json(checkpoint.model_dump_json()) == checkpoint
    for change in (
        {"seq": 0},
        {"seq": True},
        {"schema_version": 2},
        {"run_id": uuid4()},
        {"phase": "research"},
        {"state_hash": "0" * 64},
        {"schema_version": True},
    ):
        with pytest.raises(ValidationError):
            Checkpoint(**(data | change))


def test_nested_unknown_fields_and_untyped_fact_objects_are_rejected():
    data = initial_state().model_dump(mode="json")
    for change in (
        {"run_metadata": data["run_metadata"] | {"budget_reset": True}},
        {"sources": {"source-1": {"source_id": "source-1", "snippet": "not original evidence"}}},
        {"errors": [{"error": "not a Failure record"}]},
        {"section_plans": [{"section_id": "section_1"}]},
    ):
        with pytest.raises(ValidationError):
            PipelineState.model_validate(data | change)


def test_state_source_policy_must_match_frozen_selection():
    data = initial_state().model_dump(mode="json")
    with pytest.raises(ValidationError):
        PipelineState.model_validate(
            data | {"source_selection": {"categories": ["web"], "knowledge_base_ids": []}}
        )


def populated_data():
    data = initial_state().model_dump(mode="json")
    data["sources"] = {
        "s1": {
            "source_id": "s1",
            "source_type": "paper",
            "title": "Controlled fixture",
            "authors_or_publisher": ["Fixture author"],
            "published_at": None,
            "version": "v1",
            "canonical_url": "https://example.org/paper",
            "provenance": [],
            "source_tier": "unknown",
            "content_object_key": "fixtures/paper",
            "content_hash": "a" * 64,
            "data_classification": "public",
        }
    }
    data["evidence"] = {
        "e1": {
            "evidence_id": "e1",
            "source_id": "s1",
            "evidence_type": "method",
            "location": {"page_start": 1},
            "quote_or_raw_content": "Controlled fixture excerpt",
            "extraction_method": "fixture",
            "content_hash": "a" * 64,
        }
    }
    data["claims"] = {
        "c1": {
            "claim_id": "c1",
            "spec_ids": ["spec1"],
            "text": "Controlled fixture claim",
            "claim_type": "factual",
            "conditions": {"dataset": "fixture"},
            "status": "open",
            "status_reason": "Awaiting review",
        }
    }
    data["claim_evidence_links"] = [
        {
            "claim_id": "c1",
            "evidence_id": "e1",
            "relation": "supports",
            "rationale": "Controlled fixture relation",
        }
    ]
    return data


def test_nonempty_snapshot_round_trip_and_identity_closure():
    data = populated_data()
    state = PipelineState.model_validate(data)
    assert PipelineState.model_validate_json(state.model_dump_json()) == state
    for change in (
        {"sources": {"wrong-key": data["sources"]["s1"]}},
        {"sources": {}},
        {"claim_evidence_links": data["claim_evidence_links"] * 2},
        {"claims": {}},
        {"phase": "research"},
        {"schema_version": True},
        {"reviewed_draft_version": 1},
    ):
        with pytest.raises(ValidationError):
            PipelineState.model_validate(data | change)


def test_checkpoint_revalidates_mutated_nested_collections():
    state = PipelineState.model_validate(populated_data())
    state.sources.clear()
    with pytest.raises(ValidationError):
        Checkpoint(
            snapshot_id=uuid4(),
            run_id=state.run_id,
            seq=1,
            schema_version=1,
            phase="plan",
            state=state,
            state_hash=canonical_hash(state),
            created_at=datetime.now(UTC),
        )


@pytest.mark.parametrize("used", [True, -1, float("inf"), "1"])
def test_invalid_budget_usage_is_not_a_valid_snapshot(used):
    data = initial_state().model_dump(mode="json")
    data["run_metadata"]["budget_used"]["elapsed_s"] = used
    with pytest.raises(ValidationError):
        PipelineState.model_validate(data)


def test_budget_cannot_exceed_configuration():
    data = initial_state().model_dump(mode="json")
    data["run_metadata"]["budget_used"]["llm_calls"] = 61
    with pytest.raises(ValidationError):
        PipelineState.model_validate(data)


def test_workers_table_keeps_existing_phase_dispatch_contract():
    from domain.research.machine import WORKERS

    assert WORKERS == {
        "plan": "architect",
        "research": "scout",
        "analyze": "data_analyst",
        "write": "writer",
        "review": "critic",
    }
