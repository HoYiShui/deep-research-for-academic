"""Unit tests for the rework routing policy table."""

from domain.research.machine import route_after_review


def _issue(issue_type: str, severity: str = "critical", fillable: bool = False) -> dict:
    return {"issue_type": issue_type, "severity": severity, "fillable": fillable}


def test_missing_source_fillable_routes_to_re_research() -> None:
    assert route_after_review([_issue("missing_source", fillable=True)]) == "re_research"


def test_missing_source_not_fillable_routes_to_acknowledge() -> None:
    assert route_after_review([_issue("missing_source", fillable=False)]) == "acknowledge_limit"


def test_comparability_violation_routes_to_re_analyze() -> None:
    assert route_after_review([_issue("comparability_violation")]) == "re_analyze"


def test_hallucination_routes_to_re_research() -> None:
    assert route_after_review([_issue("hallucination")]) == "re_research"


def test_overclaim_routes_to_revise() -> None:
    assert route_after_review([_issue("overclaim")]) == "revise"


def test_unknown_issue_type_falls_back_to_revise() -> None:
    assert route_after_review([_issue("something_else")]) == "revise"


def test_minor_issue_does_not_trigger_rework() -> None:
    assert route_after_review([_issue("missing_source", severity="minor", fillable=True)]) == "done"


def test_no_issues_returns_done() -> None:
    assert route_after_review([]) == "done"


def test_conflicting_issues_pick_highest_priority() -> None:
    issues = [
        _issue("overclaim", severity="major"),  # revise
        _issue("missing_source", severity="critical", fillable=True),  # re_research
    ]
    assert route_after_review(issues) == "re_research"
