"""Strict mono-v1 input and persistence contracts, not legacy agent fixtures."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from application.settings import Settings
from domain.research.ids import canonical_hash, stable_id
from domain.research.state import (
    BriefRecord,
    ClarifyAssessment,
    Failure,
    PartialResearchBrief,
    ResearchBrief,
    ResearchRun,
    RunConfig,
    SessionState,
    SourceSelection,
)


def brief_data():
    return {
        "task_type": "evaluation_design",
        "decision_goal": "Select a reproducible evaluation protocol",
        "research_object": "Academic intrusion detection models",
        "scope": "Public datasets; no private data",
        "comparison_scope": "Transformer versus CNN under matched protocols",
        "claims_to_verify": "Accuracy and resource tradeoffs",
        "evidence_requirements": "Original papers with located results",
        "conclusion_boundary": "No cross-dataset ranking",
        "deliverable": "Evidence-grounded report and protocol mapping",
        "assumptions": "",
    }


def test_brief_is_exactly_ten_strings_and_trims():
    data = brief_data()
    data["scope"] = "  public  "
    brief = ResearchBrief.model_validate(data)
    assert len(brief.model_dump()) == 10
    assert brief.scope == "public"
    assert json.loads(brief.model_dump_json()) == brief.model_dump(mode="json")
    with pytest.raises(ValidationError):
        brief.scope = "changed"


@pytest.mark.parametrize(
    "change",
    [
        {"claims_to_verify": ["a"]},
        {"scope": 10},
        {"scope": "  "},
        {"task_type": "研究设计"},
        {"task_type": "reviewer_response"},
        {"query": "unknown field"},
        {"scope": "a" * 8001},
    ],
)
def test_invalid_brief_rejected(change):
    with pytest.raises(ValidationError):
        ResearchBrief.model_validate(brief_data() | change)


def test_partial_brief_omission_is_not_explicit_null():
    assert PartialResearchBrief().model_dump(exclude_unset=True) == {}
    assert PartialResearchBrief(scope="public").model_dump(exclude_unset=True) == {
        "scope": "public"
    }
    with pytest.raises(ValidationError):
        PartialResearchBrief(scope=None)
    with pytest.raises(ValidationError):
        PartialResearchBrief(query="not a brief field")


def test_source_selection_deduplicates_and_enforces_kb_relationship():
    kb = uuid4()
    selected = SourceSelection(
        categories=["knowledge_base", "knowledge_base"], knowledge_base_ids=[kb, kb]
    )
    assert selected.categories == ["knowledge_base"]
    assert selected.knowledge_base_ids == [kb]
    assert SourceSelection().categories == ["papers", "web"]
    for data in (
        {"categories": []},
        {"categories": ["knowledge_base"]},
        {"knowledge_base_ids": [str(kb)]},
        {"categories": ["papers", "invented"]},
    ):
        with pytest.raises(ValidationError):
            SourceSelection.model_validate(data)


def test_canonical_hash_is_order_invariant_and_type_sensitive():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})
    assert stable_id("ev", "a|b", "c") != stable_id("ev", "a", "b|c")
    assert stable_id("ev", 1) != stable_id("ev", "1")
    assert len(stable_id("ev", "x").split("-")[1]) == 64
    for invalid in (float("nan"), float("inf"), object()):
        with pytest.raises((TypeError, ValueError)):
            canonical_hash(invalid)


def test_frozen_brief_requires_complete_content_and_correct_hash():
    brief = ResearchBrief(**brief_data())
    data = {
        "session_id": uuid4(),
        "version": 1,
        "content": brief,
        "frozen_at": datetime.now(UTC),
        "confirmed_by": uuid4(),
        "content_hash": canonical_hash(brief),
        "source_selection": SourceSelection(),
    }
    assert isinstance(BriefRecord(**data).content, ResearchBrief)
    with pytest.raises(ValidationError):
        BriefRecord(**(data | {"content_hash": "0" * 64}))
    with pytest.raises(ValidationError):
        BriefRecord(**(data | {"content": {"scope": "public"}}))
    with pytest.raises(ValidationError):
        BriefRecord(**(data | {"frozen_at": datetime(2026, 10, 5)}))  # noqa: DTZ001 -- Invalid fixture.


def test_run_config_matches_settings_and_rejects_unknown_secrets():
    data = Settings().run_config_snapshot()
    config = RunConfig.model_validate(data)
    assert config.limits.llm_calls == 60
    with pytest.raises(ValidationError):
        Settings().run_config_snapshot(categories=["knowledge_base"], private_only=True)
    with pytest.raises(ValidationError):
        RunConfig.model_validate(data | {"api_key": "secret"})
    for invalid in (True, 0, "60"):
        changed = data | {"limits": data["limits"] | {"llm_calls": invalid}}
        with pytest.raises(ValidationError):
            RunConfig.model_validate(changed)


def test_session_has_uuid_owner_and_distinct_status():
    now = datetime.now(UTC)
    data = {
        "session_id": uuid4(),
        "owner_id": uuid4(),
        "query": "research",
        "status": "ask",
        "revision": 1,
        "brief_draft": {},
        "brief_version": 1,
        "pending_questions": [],
        "missing_fields": [],
        "clarification_round": 0,
        "clarification_limit_reached": False,
        "source_selection": {},
        "run_id": None,
        "failure": None,
        "created_at": now,
        "updated_at": now,
    }
    session = SessionState(**data)
    assert session.status == "ask"
    assert SessionState.model_validate_json(session.model_dump_json()) == session
    for change in (
        {"status": "clarify"},
        {"status": "done"},
        {"revision": True},
        {"owner_id": None},
        {"owner_id": "development-user"},
    ):
        with pytest.raises(ValidationError):
            SessionState(**(data | change))


def test_run_status_and_phase_are_distinct():
    data = {
        "run_id": uuid4(),
        "session_id": uuid4(),
        "brief_version": 1,
        "brief_hash": "a" * 64,
        "status": "ready",
        "phase": "plan",
        "attempt_count": 0,
        "checkpoint_seq": 1,
        "cancel_requested_at": None,
        "lease_owner": None,
        "lease_token": 0,
        "lease_expires_at": None,
        "resume_allowed": False,
        "failure": None,
        "config_snapshot": Settings().run_config_snapshot(),
        "created_at": datetime.now(UTC),
        "started_at": None,
        "finished_at": None,
    }
    assert ResearchRun(**data).phase == "plan"
    for change in (
        {"phase": "completed"},
        {"status": "done"},
        {"brief_hash": "md5"},
        {"checkpoint_seq": 0},
        {"attempt_count": -1},
    ):
        with pytest.raises(ValidationError):
            ResearchRun(**(data | change))


def test_assessment_has_no_model_controlled_status_and_bounded_questions():
    data = {
        "missing_fields": ["scope"],
        "questions": ["What is the scope?"],
        "brief_patch": {},
        "assumptions": [],
        "field_reasons": {"scope": "Bounds evidence"},
    }
    assert ClarifyAssessment(**data).missing_fields == ["scope"]
    for change in (
        {"status": "confirm"},
        {"questions": ["one", "two", "three"]},
        {"missing_fields": ["query"]},
        {"brief_patch": {"scope": None}},
    ):
        with pytest.raises(ValidationError):
            ClarifyAssessment(**(data | change))


def test_failure_diagnostics_are_finite_json_scalars_not_arbitrary_objects():
    data = {
        "code": "dependency_unavailable",
        "dependency": "postgres",
        "operation": "commit",
        "phase": "plan",
        "message": "Storage unavailable",
        "retryable": True,
        "resume_allowed": False,
        "attempt": 0,
        "occurred_at": datetime.now(UTC),
        "details": {"unit_ids": ["one", "two"], "duration_s": 1.2},
    }
    failure = Failure(**data)
    assert Failure.model_validate_json(failure.model_dump_json()) == failure
    for change in (
        {"details": {"nested": {"command": "run"}}},
        {"details": {"bad": float("nan")}},
        {"details": {"": "bad"}},
        {"retryable": 1},
        {"attempt": "1"},
    ):
        with pytest.raises(ValidationError):
            Failure(**(data | change))


def test_run_config_requires_all_versions_and_positive_reserved_budget():
    data = Settings().run_config_snapshot()
    for change in (
        {"versions": data["versions"] | {"prompt_versions": {"clarify": "mono-v1"}}},
        {"versions": data["versions"] | {"template_versions": {}}},
        {"limits": data["limits"] | {"terminal_reserved_calls": 60}},
        {"timeouts_s": data["timeouts_s"] | {"search": False}},
        {"source_policy": data["source_policy"] | {"private_only": True}},
    ):
        with pytest.raises(ValidationError):
            RunConfig.model_validate(data | change)
