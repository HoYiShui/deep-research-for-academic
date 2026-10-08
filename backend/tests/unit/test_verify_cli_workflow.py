"""The real-workflow probe cannot accept unrelated or altered saved reports."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from domain.research.reporting import build_report
from domain.research.state import PipelineState
from scripts.verify_cli_workflow import audit_checkpoint
from tests.report_fixtures import insufficient_review


def completed():
    before = insufficient_review()
    report = build_report(before, report_id=uuid4(), created_at=datetime.now(UTC))
    state = PipelineState.model_validate(
        before.model_dump() | {"phase": "done", "final_report": report}
    )
    result = {
        "status": "ok",
        "error": None,
        "session_id": str(state.session_id),
        "run_id": str(state.run_id),
        "checkpoint_seq": 17,
        "phase": "done",
        "final_report": report.model_dump(mode="json"),
    }
    snapshot = result | {"state": state.model_dump(mode="json")}
    return state, result, snapshot


def test_same_run_frozen_brief_and_canonical_report_are_all_audited():
    state, result, snapshot = completed()
    checks = audit_checkpoint(result, snapshot, state.research_brief)
    assert checks and all(checks.values())
    assert "quality_accepted" not in checks  # No automated scientific quality claim.


@pytest.mark.parametrize("field", ["run_id", "session_id", "checkpoint_seq", "phase"])
def test_other_identity_or_checkpoint_cannot_pass(field):
    state, result, snapshot = completed()
    snapshot[field] = 18 if field == "checkpoint_seq" else str(uuid4())
    assert not all(audit_checkpoint(result, snapshot, state.research_brief).values())


def test_different_brief_or_result_report_cannot_pass():
    state, result, snapshot = completed()
    brief = state.research_brief.model_copy(update={"scope": "A different requested scope"})
    assert not audit_checkpoint(result, snapshot, brief)["frozen_brief_matches"]
    result["final_report"]["markdown"] += "\nUnreviewed extra conclusion"
    assert not audit_checkpoint(result, snapshot, state.research_brief)["same_report"]


def test_tampered_persisted_markdown_does_not_pass_even_if_result_matches_it():
    state, result, snapshot = completed()
    snapshot["state"]["final_report"]["markdown"] += "\nUnreviewed extra conclusion"
    result["final_report"] = snapshot["state"]["final_report"]
    assert not audit_checkpoint(result, snapshot, state.research_brief)["canonical_report"]
