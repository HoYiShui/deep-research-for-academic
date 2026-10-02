"""Pure policy layer: deterministic transition rules for clarify and pipeline.

The LLM only produces judgments (data); this module applies policy (control
flow). decide_status drives clarify; next_phase drives the pipeline happy
path; route_after_review drives the rework routing table.
"""
from __future__ import annotations

from typing import Any

# Fields that must be specified before the brief is considered frozen.
CRITICAL_BRIEF_FIELDS = {"decision_goal", "research_object", "deliverable"}

# Pipeline phase order (happy path).
_PHASE_ORDER = ["plan", "research", "analyze", "write", "review", "done"]

# Rework action priority: earliest phase wins when issues conflict.
_ACTION_PRIORITY = ["re_research", "re_analyze", "revise", "acknowledge_limit"]


def decide_status(missing_fields: list[str]) -> str:
    """Decide clarify status from missing fields.

    Args:
        missing_fields: Fields the LLM judged as missing from the brief.

    Returns:
        "ask" if any critical field is missing, else "ready" (conservative
        defaults are used for non-critical gaps).
    """
    if set(missing_fields) & CRITICAL_BRIEF_FIELDS:
        return "ask"
    return "ready"


def next_phase(state: dict) -> str:
    """Return the next pipeline phase for the given state.

    Args:
        state: A PipelineState dict; reads state["phase"].

    Returns:
        The next phase name, or "done" if already at the end.
    """
    current = state.get("phase", "plan")
    try:
        idx = _PHASE_ORDER.index(current)
    except ValueError:
        return "done"
    return _PHASE_ORDER[idx + 1] if idx + 1 < len(_PHASE_ORDER) else "done"


def _action_for(issue: dict[str, Any]) -> str:
    """Map one issue to its rework action.

    The action is determined by issue_type (what kind of issue), not severity;
    severity only gates whether to rework at all. Issue types use English
    identifiers: missing_source, comparability_violation, hallucination,
    overclaim, outdated, logic_error.

    Args:
        issue: A critic feedback dict with issue_type and optional fillable.

    Returns:
        The rework action for this issue.
    """
    issue_type = issue.get("issue_type", "")
    if issue_type == "missing_source":
        return "re_research" if issue.get("fillable", False) else "acknowledge_limit"
    if issue_type == "comparability_violation":
        return "re_analyze"
    if issue_type == "hallucination":
        # Retract the claim, then re-search for real evidence.
        return "re_research"
    if issue_type == "overclaim":
        return "revise"
    if issue_type in ("outdated", "logic_error"):
        return "re_research"
    return "revise"  # default fallback


def route_after_review(issues: list[dict[str, Any]]) -> str:
    """Route critic feedback to the next pipeline action.

    Args:
        issues: List of critic feedback, each with issue_type, severity, fillable.

    Returns:
        "done" if no critical/major issue remains, else the highest-priority
        rework action (re_research > re_analyze > revise > acknowledge_limit).
    """
    actionable = [i for i in issues if i.get("severity", "minor") != "minor"]
    if not actionable:
        return "done"
    actions = [_action_for(i) for i in actionable]
    return min(actions, key=_ACTION_PRIORITY.index)


def phase_after_review(action: str) -> str:
    """Map a review route action to the next pipeline phase.

    Policy-only: the LLM never drives control flow. This table maps the
    deterministic action from route_after_review back to a phase, closing the
    rework loop (re_research -> research, re_analyze -> analyze, revise ->
    write; acknowledge_limit folds into write so the report is re-emitted).

    Args:
        action: A rework action from route_after_review.

    Returns:
        The phase to transition into, or "done" for terminal actions.
    """
    return {
        "done": "done",
        "re_research": "research",
        "re_analyze": "analyze",
        "revise": "write",
        "acknowledge_limit": "write",
    }.get(action, "done")
