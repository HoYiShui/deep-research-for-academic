"""Unit tests for the run command (T003)."""

from cli.__main__ import main


def test_run_fake_produces_report(capsys) -> None:
    code = main(["run", "test query", "--fake", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert '"status": "ok"' in out
    assert '"final_report"' in out


def test_run_requires_query_or_brief() -> None:
    assert main(["run", "--fake", "--json"]) == 2  # usage error


def test_run_rejects_query_and_brief_together() -> None:
    assert main(["run", "q", "--brief-file", "x.json", "--fake", "--json"]) == 2
