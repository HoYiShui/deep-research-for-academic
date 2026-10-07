"""Unit tests for the phase command."""

import json

from application.errors import AppError
from cli.__main__ import main
from cli.phase_tools import DebugTools
from domain.research.state import PipelineState
from tests.unit.test_state import initial_state


def test_phase_rejects_legacy_partial_review_instead_of_approving(tmp_path, capsys) -> None:
    state = {
        "session_id": "s1",
        "phase": "review",
        "draft_claim_bindings": [{"statement_id": "st1", "section_id": "s1"}],
        "claims": {"c1": {"text": "a claim"}},
        "evidence": {"e1": {"evidence_id": "e1"}},
        "sources": {"source1": {"source_id": "source1"}},
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    code = main(["phase", "review", "--state", str(path), "--json"])
    out = capsys.readouterr().out
    assert code == 2
    body = json.loads(out)
    assert body["status"] == "usage_error"
    assert "schema_version" in body["error"]["message"]


def test_phase_rejects_unknown_phase() -> None:
    assert main(["phase", "bogus", "--state", "x.json", "--json"]) == 2


def test_phase_rejects_missing_prerequisites(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"phase": "write"}))
    assert main(["phase", "write", "--state", str(path), "--json"]) == 2


def test_phase_rejects_phase_mismatch(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"phase": "plan", "research_brief": {"x": 1}}))
    assert main(["phase", "research", "--state", str(path), "--json"]) == 2


def test_phase_rejects_missing_state_file() -> None:
    assert main(["phase", "plan", "--state", "not-found.json", "--json"]) == 2


def test_private_real_phase_rejected_before_sdk_creation(monkeypatch):
    from uuid import uuid4

    import pytest

    data = initial_state().model_dump(mode="json")
    selection = {"categories": ["knowledge_base"], "knowledge_base_ids": [str(uuid4())]}
    data["source_selection"] = selection
    data["run_metadata"]["config"]["source_policy"] = selection | {"private_only": True}
    state = PipelineState.model_validate(data)

    def forbidden(*args, **kwargs):
        raise AssertionError("External SDK must not be created")

    monkeypatch.setattr("cli.phase_tools.DeepSeekLLM", forbidden)
    with pytest.raises(AppError, match="privacy_policy_conflict"):
        DebugTools(state, fake=False)
