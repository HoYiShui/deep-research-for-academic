"""Domain state: SessionState (clarify) and PipelineState (pipeline).

The two states split at the freeze point: brief_draft lives in SessionState;
the frozen brief is the input to PipelineState (never a mutable field of it).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionState:
    """Clarify-phase state, owned by session_service, stored in sessions/briefs."""

    session_id: str = ""
    brief_draft: dict = field(default_factory=dict)
    clarification_history: list[dict] = field(default_factory=list)
    status: str = "clarify"  # "clarify" | "ready"


@dataclass
class PipelineState:
    """Pipeline state, owned by orchestrator, stored in phase_snapshots."""

    session_id: str = ""
    phase: str = "plan"
    section_plans: list[dict] = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    claims: dict = field(default_factory=dict)
    run_metadata: dict = field(default_factory=dict)
