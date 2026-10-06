"""Unit tests for the frozen-Brief pipeline command."""

import json

from cli.__main__ import main


def _brief() -> dict:
    return {
        "task_type": "method_differentiation",
        "decision_goal": "compare two methods",
        "research_object": "intrusion detection",
        "scope": "academic literature",
        "comparison_scope": "transformer and CNN",
        "claims_to_verify": ["which method performs better"],
        "evidence_requirements": ["peer-reviewed papers"],
        "conclusion_boundary": "no production recommendation",
        "deliverable": "research report",
        "assumptions": ["English literature is sufficient"],
    }


def test_run_fake_produces_report(tmp_path, capsys) -> None:
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(_brief()))
    code = main(["run", "--brief", str(path), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert '"status": "ok"' in out
    assert '"final_report"' in out


def test_run_requires_brief() -> None:
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["run", "--json"])
    assert exc.value.code == 2


def test_run_rejects_non_frozen_brief(tmp_path) -> None:
    path = tmp_path / "brief.json"
    path.write_text(json.dumps({"task_type": "method_differentiation"}))
    assert main(["run", "--brief", str(path), "--json"]) == 2


def test_run_rejects_missing_brief_file() -> None:
    assert main(["run", "--brief", "not-found.json", "--json"]) == 2


def test_real_run_rejects_legacy_list_brief_before_adapters(tmp_path, capsys):
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(_brief()))
    assert main(["run", "--brief", str(path), "--real", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "validation_error"
