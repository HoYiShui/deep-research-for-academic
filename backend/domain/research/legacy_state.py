"""Temporary pre-mono state used only by the old Orchestrator/CLI.

Remove with T017/T021 migration. This is NOT a mono-v1 persistence contract.

The two states split at the freeze point: brief_draft lives in SessionState;
the frozen brief is the input to PipelineState (research_brief, read-only).
Pipeline entities are stored id-keyed so claims/evidence/metrics/artifacts/
draft_sections can be traced back to their source (traceability, charter I).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from domain.research.models import (  # noqa: F401 -- Public domain contracts.
    BriefRecord,
    ClarifyAssessment,
    Failure,
    Message,
    PartialResearchBrief,
    ResearchBrief,
    ResearchRun,
    RunConfig,
    SessionState,
    SourceSelection,
)

# ---- Entity contracts (field-level schema, aligned with data-model.md) ----


@dataclass
class SourceRecord:
    """A registered source (each source is registered once)."""

    source_id: str = ""
    source_type: str = ""  # paper / dataset / code / standard / local_document
    title: str = ""
    authors_or_publisher: str = ""
    published_at: str = ""
    version: str = ""
    canonical_url: str = ""
    provenance: str = ""
    source_tier: str = "unknown"  # primary / official / peer_reviewed / secondary / unknown


@dataclass
class Evidence:
    """A minimal citable unit with a source location."""

    evidence_id: str = ""
    source_id: str = ""
    evidence_type: str = ""
    location: str = ""  # page / table / line number
    quote_or_raw_content: str = ""
    extraction_method: str = ""


@dataclass
class Claim:
    """A research assertion, supported/limited/refuted by evidence."""

    claim_id: str = ""
    text: str = ""
    conditions: dict = field(default_factory=dict)
    status: str = "open"  # open / supported / limited / refuted / insufficient


@dataclass
class ClaimEvidenceLink:
    """A claim -> evidence relation."""

    claim_id: str = ""
    evidence_id: str = ""
    relation: str = "supports"  # supports / refutes / limits


@dataclass
class QuantitativeObservation:
    """A structured projection of a result-table cell (back-linked to evidence)."""

    observation_id: str = ""
    evidence_id: str = ""
    kind: str = ""
    row_key: str = ""
    column_key: str = ""
    value: str = ""
    uncertainty: str = ""
    statistic: str = ""


@dataclass
class ComparableMetric:
    """A normalized metric with a comparability verdict."""

    comparable_metric_id: str = ""
    observation_ids: list = field(default_factory=list)
    metric_definition: str = ""
    evaluated_method: str = ""
    evaluation_context: dict = field(default_factory=dict)
    value: str = ""
    unit: str = ""
    comparability: str = "compatible"  # compatible / partial / incompatible
    reasons: list = field(default_factory=list)


@dataclass
class AnalysisArtifact:
    """A controlled analysis output with input provenance."""

    artifact_id: str = ""
    section_id: str = ""
    input_metric_ids: list = field(default_factory=list)
    input_evidence_ids: list = field(default_factory=list)
    operation: str = ""  # comparison_matrix / pairwise_delta / plot / statistic / aggregation
    code_or_recipe: str = ""
    output: dict = field(default_factory=dict)
    execution_status: str = "completed"  # completed / failed


@dataclass
class DraftSection:
    """A section draft."""

    section_id: str = ""
    title: str = ""
    content: str = ""


@dataclass
class DraftClaimBinding:
    """A program-level binding of a draft conclusion to claims/evidence/artifacts."""

    section_id: str = ""
    statement_id: str = ""
    claim_ids: list = field(default_factory=list)
    cited_evidence_ids: list = field(default_factory=list)
    artifact_ids: list = field(default_factory=list)


@dataclass
class CriticFeedback:
    """A review issue (judgment only; routing is the policy table's job)."""

    issue_id: str = ""
    target_type: str = ""  # source / evidence / claim / artifact / draft_section
    target_id: str = ""
    issue_type: str = ""  # missing_source / comparability_violation / overclaim / hallucination / outdated / logic_error
    severity: str = "minor"  # critical / major / minor
    fillable: bool = False  # only meaningful for missing_source
    description: str = ""
    resolved: bool = False


@dataclass
class SectionCoverage:
    """Per-section coverage index (covered claims + gaps)."""

    section_id: str = ""
    covered_claim_ids: list = field(default_factory=list)
    gaps: list = field(default_factory=list)


@dataclass
class PipelineState:
    """Pipeline state, owned by orchestrator, stored in phase_snapshots.

    research_brief is read-only input; everything else is produced phase by
    phase. Id-keyed fields (sources/evidence/claims/observations/metrics/
    artifacts/draft_sections/coverage) enable traceability (charter I).
    """

    session_id: str = ""
    phase: str = "plan"
    research_brief: dict = field(default_factory=dict)  # frozen ResearchBrief (input)
    section_plans: list = field(default_factory=list)  # list[SectionPlan]
    sources: dict = field(default_factory=dict)  # dict[source_id, SourceRecord]
    evidence: dict = field(default_factory=dict)  # dict[evidence_id, Evidence]
    claims: dict = field(default_factory=dict)  # dict[claim_id, Claim]
    claim_evidence_links: list = field(default_factory=list)  # list[ClaimEvidenceLink]
    quantitative_observations: dict = field(default_factory=dict)  # dict[observation_id, QuantitativeObservation]
    comparable_metrics: dict = field(default_factory=dict)  # dict[metric_id, ComparableMetric]
    analysis_artifacts: dict = field(default_factory=dict)  # dict[artifact_id, AnalysisArtifact]
    draft_sections: dict = field(default_factory=dict)  # dict[section_id, DraftSection]
    draft_claim_bindings: list = field(default_factory=list)  # list[DraftClaimBinding]
    critic_feedback: list = field(default_factory=list)  # list[CriticFeedback]
    final_report: dict | None = None  # FinalReport
    section_coverage: dict = field(default_factory=dict)  # dict[section_id, SectionCoverage]
    run_metadata: dict = field(default_factory=dict)  # RunMetadata
    errors: list = field(default_factory=list)  # failure-semantics errors
