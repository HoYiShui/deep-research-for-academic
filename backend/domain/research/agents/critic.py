"""Critic agent: review draft bindings and produce judgment.

Produces issue_type/severity/fillable only; never required_action -- routing
is the policy table's job (machine.route_after_review).
"""
from __future__ import annotations

from typing import Any


def review(bindings: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Review each binding for unsupported evidence references.

    Args:
        bindings: DraftClaimBinding list.
        evidence: Evidence list.

    Returns:
        A list of CriticFeedback dicts (issue_type, severity, fillable).
    """
    known = {ev["source_id"] for ev in evidence}
    feedback: list[dict[str, Any]] = []
    for binding in bindings:
        for evidence_id in binding.get("cited_evidence_ids", []):
            if evidence_id not in known:
                feedback.append(
                    {
                        "issue_id": f"i-{binding['statement_id']}",
                        "issue_type": "missing_source",
                        "severity": "critical",
                        "fillable": True,
                    }
                )
    return feedback
