"""Complete mono-v1 snapshots. Legacy callers are isolated until T017/T021."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictFloat, StrictInt, field_validator, model_validator

from domain.research.facts import (
    AnalysisArtifact,
    Claim,
    ClaimEvidenceLink,
    ComparableMetric,
    ComparisonSet,
    CriticFeedback,
    DraftClaimBinding,
    DraftSection,
    Evidence,
    FinalReport,
    QuantitativeObservation,
    ReworkTarget,
    SectionCoverage,
    SectionID,
    SectionPlan,
    SourceRecord,
)
from domain.research.ids import canonical_hash
from domain.research.models import (  # noqa: F401 -- Public lifecycle contracts.
    UTC,
    BriefRecord,
    ClarifyAssessment,
    Failure,
    Hash,
    Message,
    Nonnegative,
    PartialResearchBrief,
    Positive,
    Record,
    ResearchBrief,
    ResearchPhase,
    ResearchRun,
    ReviewVerdict,
    RunConfig,
    SessionState,
    SourceSelection,
    Text,
)


class BudgetUsage(Record):
    llm_calls: Nonnegative
    search_calls: Nonnegative
    fetch_calls: Nonnegative
    tokens: Nonnegative
    elapsed_s: Annotated[StrictFloat | StrictInt, Field(ge=0, allow_inf_nan=False)]


class VersionReference(Record):
    kb_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_version: Text


class Degradation(Record):
    source: Text
    reason: Text
    operation: Text
    section_id: SectionID | None
    occurred_at: UTC


class UnitResult(Record):
    unit_id: Text
    phase: ResearchPhase
    input_hash: Hash
    result_hash: Hash
    checkpoint_seq: Positive
    completed_at: UTC
    affected_ids: list[Text]


class RunMetadata(Record):
    config: RunConfig
    budget_used: BudgetUsage
    rework_count: Nonnegative
    rework_targets: list[ReworkTarget]
    degraded_sources: list[Degradation]
    unit_manifest: dict[Text, UnitResult]
    knowledge_snapshot: list[VersionReference]
    stop_reason: Text | None = None

    @model_validator(mode="after")
    def check_accounting(self):
        for name in ("llm_calls", "search_calls", "fetch_calls", "tokens"):
            if getattr(self.budget_used, name) > getattr(self.config.limits, name):
                raise ValueError(f"Budget exceeded: {name}")
        if self.rework_count > self.config.limits.rework_rounds:
            raise ValueError("Rework budget exceeded")
        if any(key != unit.unit_id for key, unit in self.unit_manifest.items()):
            raise ValueError("Manifest key differs from unit ID")
        if any(
            version.kb_id not in self.config.source_policy.knowledge_base_ids
            for version in self.knowledge_snapshot
        ):
            raise ValueError("Knowledge snapshot outside frozen scope")
        return self


class PipelineState(Record):
    """All keys are required on read; only initial() fills empty outputs."""

    schema_version: Literal[1]
    session_id: UUID
    run_id: UUID
    brief_version: Positive
    brief_hash: Hash
    phase: ResearchPhase
    research_brief: ResearchBrief
    source_selection: SourceSelection
    section_plans: list[SectionPlan]
    sources: dict[Text, SourceRecord]
    evidence: dict[Text, Evidence]
    claims: dict[Text, Claim]
    claim_evidence_links: list[ClaimEvidenceLink]
    quantitative_observations: dict[Text, QuantitativeObservation]
    comparable_metrics: dict[Text, ComparableMetric]
    comparison_sets: dict[Text, ComparisonSet]
    analysis_artifacts: dict[Text, AnalysisArtifact]
    section_coverage: dict[SectionID, SectionCoverage]
    draft_sections: dict[SectionID, DraftSection]
    draft_claim_bindings: list[DraftClaimBinding]
    critic_feedback: list[CriticFeedback]
    draft_version: Nonnegative
    reviewed_draft_version: Positive | None
    review_verdict: ReviewVerdict | None
    final_report: FinalReport | None
    run_metadata: RunMetadata
    errors: list[Failure]

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported schema version")
        return value

    @classmethod
    def initial(
        cls, *, session_id, run_id, brief_version, research_brief, source_selection, config
    ):
        brief = ResearchBrief.model_validate(research_brief)
        return cls(
            schema_version=1,
            session_id=session_id,
            run_id=run_id,
            brief_version=brief_version,
            brief_hash=canonical_hash(brief),
            phase="plan",
            research_brief=brief,
            source_selection=source_selection,
            section_plans=[],
            sources={},
            evidence={},
            claims={},
            claim_evidence_links=[],
            quantitative_observations={},
            comparable_metrics={},
            comparison_sets={},
            analysis_artifacts={},
            section_coverage={},
            draft_sections={},
            draft_claim_bindings=[],
            critic_feedback=[],
            draft_version=0,
            reviewed_draft_version=None,
            review_verdict=None,
            final_report=None,
            errors=[],
            run_metadata={
                "config": config,
                "budget_used": {
                    "llm_calls": 0,
                    "search_calls": 0,
                    "fetch_calls": 0,
                    "tokens": 0,
                    "elapsed_s": 0,
                },
                "rework_count": 0,
                "rework_targets": [],
                "degraded_sources": [],
                "unit_manifest": {},
                "knowledge_snapshot": [],
                "stop_reason": None,
            },
        )

    @model_validator(mode="after")
    def check_snapshot(self):
        if self.brief_hash != canonical_hash(self.research_brief):
            raise ValueError("Frozen research brief hash mismatch")
        if self.section_plans:
            if {plan.section_id for plan in self.section_plans} != {
                "section_1",
                "section_2",
                "section_3",
                "section_4",
                "section_5",
            } or len(self.section_plans) != 5:
                raise ValueError("Plan must cover exactly five sections")
            if not any(plan.claim_specs for plan in self.section_plans) or not any(
                plan.sub_questions for plan in self.section_plans
            ):
                raise ValueError("Plan needs a claim spec and retrieval question")
        elif self.phase != "plan":
            raise ValueError("Cannot advance an empty plan")
        policy = self.run_metadata.config.source_policy
        if (
            self.source_selection.categories != policy.categories
            or self.source_selection.knowledge_base_ids != policy.knowledge_base_ids
        ):
            raise ValueError("Run source policy differs from frozen selection")
        for collection, id_field in (
            (self.sources, "source_id"),
            (self.evidence, "evidence_id"),
            (self.claims, "claim_id"),
            (self.quantitative_observations, "observation_id"),
            (self.comparable_metrics, "comparable_metric_id"),
            (self.comparison_sets, "comparison_set_id"),
            (self.analysis_artifacts, "artifact_id"),
            (self.section_coverage, "section_id"),
            (self.draft_sections, "section_id"),
        ):
            if any(key != getattr(record, id_field) for key, record in collection.items()):
                raise ValueError(f"Collection key differs from {id_field}")
        if any(ev.source_id not in self.sources for ev in self.evidence.values()):
            raise ValueError("Evidence references an unregistered source")
        relations = [
            (link.claim_id, link.evidence_id, link.relation) for link in self.claim_evidence_links
        ]
        if len(set(relations)) != len(relations):
            raise ValueError("Duplicate claim-evidence relation")
        if any(
            link.claim_id not in self.claims or link.evidence_id not in self.evidence
            for link in self.claim_evidence_links
        ):
            raise ValueError("Dangling claim-evidence link")
        if any(
            obs.evidence_id not in self.evidence for obs in self.quantitative_observations.values()
        ):
            raise ValueError("Observation references missing evidence")
        if any(
            obs_id not in self.quantitative_observations
            for metric in self.comparable_metrics.values()
            for obs_id in metric.observation_ids
        ):
            raise ValueError("Metric references missing observations")
        if any(
            metric_id not in self.comparable_metrics
            for group in self.comparison_sets.values()
            for metric_id in group.metric_ids
        ):
            raise ValueError("Comparison set references missing metrics")
        for artifact in self.analysis_artifacts.values():
            group = self.comparison_sets.get(artifact.comparison_set_id)
            if group is None or group.section_id != artifact.section_id:
                raise ValueError("Artifact references missing or wrong-section comparison set")
            if not set(artifact.input_metric_ids) <= set(group.metric_ids) or not set(
                artifact.input_evidence_ids
            ) <= set(self.evidence):
                raise ValueError("Artifact input chain is incomplete")
        if any(
            section.draft_version != self.draft_version for section in self.draft_sections.values()
        ):
            raise ValueError("Draft sections must share the current version")
        if any(
            binding.draft_version != self.draft_version for binding in self.draft_claim_bindings
        ):
            raise ValueError("Bindings must share the current version")
        for binding in self.draft_claim_bindings:
            section = self.draft_sections.get(binding.section_id)
            if section is None or binding.statement_id not in {
                statement.statement_id for statement in section.statements
            }:
                raise ValueError("Binding does not locate a draft statement")
            if (
                not set(binding.claim_ids) <= set(self.claims)
                or not set(binding.cited_evidence_ids) <= set(self.evidence)
                or not set(binding.artifact_ids) <= set(self.analysis_artifacts)
            ):
                raise ValueError("Binding references missing facts")
        if (
            self.reviewed_draft_version is not None
            and self.reviewed_draft_version != self.draft_version
        ):
            raise ValueError("Review targets a stale draft")
        if self.final_report is not None:
            report = self.final_report
            if (
                self.phase != "done"
                or report.run_id != self.run_id
                or report.session_id != self.session_id
                or report.brief_version != self.brief_version
                or report.draft_version != self.draft_version
                or report.review_verdict != self.review_verdict
            ):
                raise ValueError("Report identity or delivery state mismatch")
        return self


class Checkpoint(Record):
    snapshot_id: UUID
    run_id: UUID
    seq: Positive
    schema_version: Literal[1]
    phase: ResearchPhase
    state: PipelineState
    state_hash: Hash
    created_at: UTC

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported schema version")
        return value

    @model_validator(mode="after")
    def check_state_identity(self):
        if self.run_id != self.state.run_id or self.phase != self.state.phase:
            raise ValueError("Checkpoint identity/phase differs from state")
        if self.state_hash != canonical_hash(self.state):
            raise ValueError("Checkpoint state hash mismatch")
        return self
