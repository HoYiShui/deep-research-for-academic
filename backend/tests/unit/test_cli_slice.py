"""Unit tests for the phase command."""

import json

from cli.__main__ import main


def test_phase_review_fake(tmp_path, capsys) -> None:
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
    assert code == 0
    assert '"phase": "review"' in out
    assert '"state_delta"' in out


def test_phase_rejects_unknown_phase() -> None:
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["phase", "bogus", "--state", "x.json", "--json"])
    assert exc.value.code == 2


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
