"""Critic agent: review draft bindings and produce judgment.

Reviews draft claim bindings against claims/evidence/sources (three layers:
source support, condition boundaries, analysis overreach). Produces
issue_type/severity/fillable only; never required_action -- routing is the
policy table's job (machine.route_after_review).
"""
from __future__ import annotations

from typing import Any

from domain.ports import LLMPort
from domain.research.agents.base import call_llm, parse_json

# The six issue types (aligned with data-model.md); missing_source is the only
# one that carries a fillable flag.
_ISSUE_TYPES = {
    "missing_source",
    "comparability_violation",
    "overclaim",
    "hallucination",
    "outdated",
    "logic_error",
}


async def review(
    bindings: list[dict[str, Any]],
    claims: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    llm: LLMPort,
) -> list[dict[str, Any]]:
    """Review bindings, combining a deterministic source check with LLM review.

    Args:
        bindings: DraftClaimBinding list.
        claims: id-keyed Claim dict.
        evidence: id-keyed Evidence dict.
        sources: id-keyed SourceRecord dict.
        llm: LLMPort (three-layer review).

    Returns:
        A list of CriticFeedback dicts (issue_type, severity, fillable).
    """
    feedback = _missing_source_check(bindings, evidence)
    if bindings:
        judgment = parse_json(
            await call_llm(llm, _review_prompt(bindings, claims, evidence, sources))
        )
        for item in judgment.get("issues", []):
            issue_type = item.get("issue_type", "")
            if issue_type not in _ISSUE_TYPES:
                continue
            feedback.append(
                {
                    "issue_id": stable_issue_id(item),
                    "target_type": item.get("target_type", "draft_section"),
                    "target_id": item.get("target_id", ""),
                    "issue_type": issue_type,
                    "severity": item.get("severity", "minor"),
                    "fillable": item.get("fillable", False) and issue_type == "missing_source",
                    "description": item.get("description", ""),
                    "resolved": False,
                }
            )
    return feedback


def _missing_source_check(
    bindings: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Deterministic check: every cited evidence id must resolve."""
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


def stable_issue_id(item: dict[str, Any]) -> str:
    """Derive a stable issue id from target + issue type."""
    return f"i-{item.get('target_id', '')}-{item.get('issue_type', '')}"


def _review_prompt(
    bindings: list[dict[str, Any]],
    claims: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> str:
    """Build the three-layer review prompt."""
    binding_lines = "\n".join(
        f"- [{b.get('statement_id')}] section {b.get('section_id')}: "
        f"claims {b.get('claim_ids')} evidence {b.get('cited_evidence_ids')}"
        for b in bindings[:20]
    )
    claim_lines = "\n".join(
        f"- [{cid}] {c.get('text', '')}" for cid, c in list(claims.items())[:20]
    )
    return (
        "You are a research quality critic. Review the draft bindings against the "
        "claims and evidence for: (1) source/evidence support, (2) claim condition "
        "boundaries (dataset/protocol/metric), (3) analysis/report overreach. Flag "
        "issues only where there is a concrete problem. Respond with JSON only:\n"
        '{"issues": [{"target_type": "draft_section", "target_id": "...", '
        '"issue_type": "missing_source|comparability_violation|overclaim|hallucination|'
        'outdated|logic_error", "severity": "critical|major|minor", "fillable": false, '
        '"description": "..."}]}\n\n'
        f"Bindings:\n{binding_lines}\n\nClaims:\n{claim_lines}\n"
    )
