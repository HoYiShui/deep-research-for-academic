"""Domain events: derived from state transitions and streamed via SSE."""

from __future__ import annotations

from dataclasses import dataclass, field


# Single source of truth for frontend display names.
PHASE_DISPLAY: dict[str, str] = {
    "plan": "planning",
    "research": "researching",
    "analyze": "analyzing",
    "write": "writing",
    "review": "reviewing",
    "done": "done",
}


@dataclass
class PhaseEvent:
    """A phase transition event."""

    phase: str
    message: str = ""


@dataclass
class StepEvent:
    """A step-level progress event within a section."""

    section_id: str
    stage: str
    detail: dict = field(default_factory=dict)


@dataclass
class ReworkEvent:
    """A review-driven rework routing event."""

    issue_id: str
    action: str
    target: str


@dataclass
class DoneEvent:
    """Completion event carrying the report URL."""

    report_url: str
