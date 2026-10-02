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
    """Pipeline state, owned by orchestrator, stored in phase_snapshots.

    The frozen brief is read-only input; everything else is produced phase by
    phase and snapshotted at each phase boundary for recovery.
    """

    session_id: str = ""
    phase: str = "plan"
    brief: dict = field(default_factory=dict)  # frozen ResearchBrief (input)
    section_plans: list = field(default_factory=list)  # SectionPlan list
    evidence: list = field(default_factory=list)  # Evidence list (deduplicated)
    coverage_gaps: list = field(default_factory=list)  # unfilled gaps (search/evidence)
    comparable_metrics: list = field(default_factory=list)  # ComparableMetric list
    analysis_artifacts: list = field(default_factory=list)  # AnalysisArtifact list
    draft_sections: list = field(default_factory=list)  # DraftSection list
    draft_claim_bindings: list = field(default_factory=list)  # DraftClaimBinding list
    critic_feedback: list = field(default_factory=list)  # CriticFeedback list
    final_report: dict | None = None  # FinalReport
    run_metadata: dict = field(default_factory=dict)  # RunMetadata
    errors: list = field(default_factory=list)  # failure-semantics errors
