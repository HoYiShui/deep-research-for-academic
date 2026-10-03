"""Critic agent: review draft bindings and produce judgment.

Produces issue_type/severity/fillable only; never required_action -- routing
is the policy table's job (machine.route_after_review).
"""
from __future__ import annotations

from typing import Any


def review(bindings: list[dict[str, Any]], evidence: dict[str, dict]) -> list[dict[str, Any]]:
    """Review each binding for unsupported evidence references.

    Args:
        bindings: DraftClaimBinding list.
        evidence: id-keyed Evidence dict.

    Returns:
        A list of CriticFeedback dicts (issue_type, severity, fillable).
    """
    feedback: list[dict[str, Any]] = []
    for binding in bindings:
        for evidence_id in binding.get("cited_evidence_ids", []):
            if evidence_id not in evidence:
                feedback.append(
                    {
                        "issue_id": f"i-{binding['statement_id']}",
                        "target_type": "draft_section",
                        "target_id": binding.get("section_id", ""),
                        "issue_type": "missing_source",
                        "severity": "critical",
                        "fillable": True,
                        "description": f"evidence {evidence_id} not found",
                        "resolved": False,
                    }
                )
    return feedback
