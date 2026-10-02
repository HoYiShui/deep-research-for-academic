"""Pure policy layer: deterministic transition rules for clarify and pipeline.

The LLM only produces judgments (data); this module applies policy (control
flow). decide_status drives clarify; next_phase drives the pipeline happy path.
The rework routing table (_route_after_review) is added in a later task.
"""
from __future__ import annotations

# Fields that must be specified before the brief is considered frozen.
CRITICAL_BRIEF_FIELDS = {"decision_goal", "research_object", "deliverable"}

# Minimal pipeline phase order (happy path; no rework yet).
_PHASE_ORDER = ["plan", "research", "write", "review", "done"]


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
