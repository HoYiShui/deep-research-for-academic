"""CLI-only validation for frozen Briefs and phase-debug state files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cli import output
from domain.research.phase_contracts import PhaseInput
from domain.research.state import PipelineState

_BRIEF_FIELDS = (
    "task_type",
    "decision_goal",
    "research_object",
    "scope",
    "comparison_scope",
    "claims_to_verify",
    "evidence_requirements",
    "conclusion_boundary",
    "deliverable",
    "assumptions",
)
_TASK_TYPES = {
    "idea_exploration",
    "method_differentiation",
    "evaluation_design",
    "reviewer_response",
}


def read_json(path: str, option: str) -> Any:
    """Load a JSON input file and classify file problems as CLI usage errors."""
    try:
        with Path(path).open() as file:
            return json.load(file)
    except (OSError, UnicodeError) as exc:
        raise output.UsageError(f"{option} file cannot be read: {path}") from exc
    except json.JSONDecodeError as exc:
        raise output.UsageError(f"{option} must contain valid JSON: {exc.msg}") from exc


def validate_brief(data: Any) -> dict:
    """Return a frozen ResearchBrief or raise a CLI usage error."""
    if not isinstance(data, dict):
        raise output.UsageError("--brief must contain a JSON object")
    missing = [name for name in _BRIEF_FIELDS if name not in data or data[name] is None]
    if missing:
        raise output.UsageError(f"--brief is not frozen; missing fields: {', '.join(missing)}")
    if data["task_type"] not in _TASK_TYPES:
        choices = ", ".join(sorted(_TASK_TYPES))
        raise output.UsageError(f"--brief task_type must be one of: {choices}")
    return data


def load_phase_state(data: Any, phase: str) -> PipelineState:
    """Validate a debug snapshot before dispatching a single phase.

    This intentionally belongs to the CLI harness, not the production state
    machine: it prevents incomplete canned input from looking like a successful
    agent run.
    """
    if not isinstance(data, dict):
        raise output.UsageError("--state must contain a JSON object")
    # CLI dump/phase envelopes are convenient debug inputs; only the inner
    # canonical state is authoritative, never envelope status or metadata.
    if data.get("status") in {"ok", "failed", "env_error"} and isinstance(data.get("state"), dict):
        data = data["state"]
    try:
        state = PipelineState.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in item["loc"]) for item in exc.errors()})
        raise output.UsageError("Invalid mono --state fields: " + ", ".join(fields)) from None
    if state.phase != phase:
        raise output.UsageError(f"--state phase is {state.phase!r}; command requests {phase!r}")
    try:
        PhaseInput.from_state(state)
    except ValueError:
        raise output.UsageError(
            f"--state lacks valid {phase} prerequisites or fact links"
        ) from None
    return state


def state_delta(before: dict, after: dict) -> dict:
    """Return changed top-level PipelineState values for concise CLI output."""
    return {name: after[name] for name in after if before.get(name) != after[name]}
