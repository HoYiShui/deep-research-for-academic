"""Structural mono-v1 fact contracts; agents must additionally prove provenance.

Passing these schemas is necessary, not proof of a real fetched quote or a
valid statistical comparison. Those checks belong to the producing services.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, Field, StrictBool, model_validator

from domain.research.models import (
    UTC,
    Failure,
    Hash,
    Operation,
    Positive,
    Record,
    ReviewVerdict,
    Text,
)

SectionID = Literal["section_1", "section_2", "section_3", "section_4", "section_5"]
SourceTier = Literal["primary", "official", "peer_reviewed", "secondary", "unknown"]
IssueType = Literal[
    "missing_source",
    "comparability_violation",
    "overclaim",
    "logic_error",
    "hallucination",
    "outdated",
]


def attributes(value):
    """Only bounded-by-caller research attributes, never nested control JSON."""
    if not isinstance(value, dict):
        raise ValueError("Attributes must be an object")  # noqa: TRY004 -- Pydantic validation error.
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Attribute keys must be nonempty strings")
        values = item if isinstance(item, list) else [item]
        for scalar in values:
            if type(scalar) not in (str, int, float, bool, type(None)):
                raise ValueError("Only JSON scalars and scalar lists are allowed")
            if isinstance(scalar, float) and not Decimal(str(scalar)).is_finite():
                raise ValueError("Nonfinite attributes are forbidden")
    return value


Attributes = Annotated[dict, AfterValidator(attributes)]
NonemptyTexts = Annotated[list[Text], Field(min_length=1)]


def decimal_string(value):
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal string") from exc
    if not number.is_finite():
        raise ValueError("Nonfinite decimal string")
    return value


DecimalString = Annotated[Text, AfterValidator(decimal_string)]


class ClaimSpec(Record):
    spec_id: Text
    text: Text
    required_conditions: list[Text]
    required_source_tiers: list[SourceTier]


class MatrixParameters(Record):
    columns: NonemptyTexts


class PairwisePlanParameters(Record):
    left_claim_spec_id: Text
    right_claim_spec_id: Text
    mode: Literal["absolute", "relative_percent"]


class PairwiseParameters(Record):
    left_metric_id: Text
    right_metric_id: Text
    mode: Literal["absolute", "relative_percent"]


class PlotParameters(Record):
    kind: Literal["bar", "line"]
    x_field: Text
    y_field: Literal["value"]
    include_uncertainty: StrictBool


class StatisticParameters(Record):
    kind: Literal["mean", "median", "min", "max"]
    group_by: list[Text]


class AggregationParameters(Record):
    kind: Literal["count"]
    group_by: list[Text]


Parameters = (
    MatrixParameters
    | PairwiseParameters
    | PlotParameters
    | StatisticParameters
    | AggregationParameters
)
PlanParameters = (
    MatrixParameters
    | PairwisePlanParameters
    | PlotParameters
    | StatisticParameters
    | AggregationParameters
)
PARAMETER_TYPES = {
    "comparison_matrix": MatrixParameters,
    "pairwise_delta": PairwiseParameters,
    "plot": PlotParameters,
    "statistic": StatisticParameters,
    "aggregation": AggregationParameters,
}


class AnalysisRequirement(Record):
    requirement_id: Text
    operation: Operation
    claim_spec_ids: NonemptyTexts
    required_context_fields: list[Text]
    parameters: PlanParameters

    @model_validator(mode="after")
    def check_parameters(self):
        expected = (
            PairwisePlanParameters
            if self.operation == "pairwise_delta"
            else PARAMETER_TYPES[self.operation]
        )
        if not isinstance(self.parameters, expected):
            raise ValueError("Operation parameters do not match")  # noqa: TRY004
        if isinstance(self.parameters, PairwisePlanParameters) and not {
            self.parameters.left_claim_spec_id,
            self.parameters.right_claim_spec_id,
        } <= set(self.claim_spec_ids):
            raise ValueError("Pairwise plan references outside claim specs")
        return self


class SectionPlan(Record):
    section_id: SectionID
    title: Text
    objective: Text
    claim_specs: list[ClaimSpec]
    sub_questions: list[Text]
    retrieval_anchors: list[Text]
    evidence_requirements: list[Text]
    analysis_requirements: list[AnalysisRequirement]


class Provenance(Record):
    retrieved_at: UTC
    retrieved_via: Literal["papers", "web", "knowledge_base", "citation_trace", "gap_fill"]
    original_ref: Text
    document_version_id: UUID | None
    upstream_source_id: Text | None


class SourceRecord(Record):
    source_id: Text
    source_type: Literal["paper", "web", "dataset", "code", "standard", "local_document"]
    title: Text
    authors_or_publisher: list[Text]
    published_at: Text | None
    version: Text | None
    canonical_url: Text | None
    provenance: list[Provenance]
    source_tier: SourceTier
    content_object_key: Text | None
    content_hash: Hash | None
    data_classification: Literal["public", "private"]


class Location(Record):
    page_start: Positive | None = None
    page_end: Positive | None = None
    section: Text | None = None
    table: Text | None = None
    file: Text | None = None
    commit: Text | None = None
    selector: Text | None = None
    line_start: Positive | None = None
    line_end: Positive | None = None
    chunk_id: Text | None = None

    @model_validator(mode="after")
    def check_location(self):
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("Evidence must have a location")
        for start, end in ((self.page_start, self.page_end), (self.line_start, self.line_end)):
            if end is not None and (start is None or end < start):
                raise ValueError("Invalid location range")
        return self


EvidenceType = Literal[
    "method",
    "protocol",
    "result_table",
    "limitation",
    "dataset_description",
    "code_configuration",
    "standard_clause",
    "other",
]
ClaimType = Literal["factual", "empirical_comparison", "hypothesis", "recommendation"]
ObservationKind = Literal["benchmark_result", "dataset_stat", "hyperparameter", "resource_cost"]


class Evidence(Record):
    evidence_id: Text
    source_id: Text
    evidence_type: EvidenceType
    location: Location
    quote_or_raw_content: Text
    extraction_method: Text
    content_hash: Hash


class Claim(Record):
    claim_id: Text
    spec_ids: NonemptyTexts
    text: Text
    claim_type: ClaimType
    conditions: Attributes
    status: Literal["open", "supported", "limited", "refuted", "insufficient"]
    status_reason: Text


class ClaimEvidenceLink(Record):
    claim_id: Text
    evidence_id: Text
    relation: Literal["supports", "refutes", "limits"]
    rationale: Text


class Gap(Record):
    gap_id: Text
    section_id: SectionID
    claim_spec_id: Text | None
    claim_id: Text | None
    reason: Text
    fillable: StrictBool
    verification_action: Text


class SectionCoverage(Record):
    section_id: SectionID
    claim_spec_ids: list[Text]
    claim_ids: list[Text]
    evidence_ids: list[Text]
    covered_claim_ids: list[Text]
    gaps: list[Gap]
    unresolved_items: list[Text]


class QuantitativeObservation(Record):
    observation_id: Text
    evidence_id: Text
    kind: ObservationKind
    row_key: Attributes
    column_key: Attributes
    raw_value: Text
    value: DecimalString | None
    uncertainty: DecimalString | None
    statistic: Text
    context: Attributes
    unit: Text | None


class ComparableMetric(Record):
    comparable_metric_id: Text
    observation_ids: NonemptyTexts
    metric_definition: Text
    evaluated_method: Text
    evaluation_context: Attributes
    value: DecimalString | None
    unit: Text | None
    normalization_basis: Text
    missing_context_fields: list[Text]


class ComparisonSet(Record):
    comparison_set_id: Text
    section_id: SectionID
    requirement_id: Text
    metric_ids: list[Text]
    required_context_fields: list[Text]
    comparability: Literal["compatible", "partial", "incompatible"]
    reasons: NonemptyTexts


class AnalysisSpec(Record):
    section_id: SectionID
    requirement_id: Text
    comparison_set_id: Text
    operation: Operation
    metric_ids: NonemptyTexts
    parameters: Parameters
    template_version: Text

    @model_validator(mode="after")
    def check_parameters(self):
        if not isinstance(self.parameters, PARAMETER_TYPES[self.operation]):
            raise ValueError("Operation parameters do not match")  # noqa: TRY004
        if isinstance(self.parameters, PairwiseParameters) and not {
            self.parameters.left_metric_id,
            self.parameters.right_metric_id,
        } <= set(self.metric_ids):
            raise ValueError("Pairwise execution references outside metric set")
        return self


class MatrixRow(Record):
    metric_id: Text
    cells: dict[Text, Text | None]


class MatrixOutput(Record):
    columns: NonemptyTexts
    rows: list[MatrixRow]


class PairwiseOutput(Record):
    left_metric_id: Text
    right_metric_id: Text
    value: DecimalString
    unit: Text


class PlotPoint(Record):
    metric_id: Text
    x: Text
    y: DecimalString
    uncertainty: DecimalString | None


class PlotOutput(Record):
    points: list[PlotPoint]
    files: list[Text]


class CountGroup(Record):
    key: dict[Text, Text]
    metric_ids: list[Text]
    count: Positive


class StatisticGroup(CountGroup):
    value: DecimalString
    unit: Text


class StatisticOutput(Record):
    groups: list[StatisticGroup]


class AggregationOutput(Record):
    groups: list[CountGroup]


OUTPUT_TYPES = {
    "comparison_matrix": MatrixOutput,
    "pairwise_delta": PairwiseOutput,
    "plot": PlotOutput,
    "statistic": StatisticOutput,
    "aggregation": AggregationOutput,
}


class AnalysisArtifact(Record):
    artifact_id: Text
    section_id: SectionID
    input_metric_ids: list[Text]
    input_evidence_ids: list[Text]
    comparison_set_id: Text
    operation: Operation
    code_or_recipe: Text
    template_version: Text
    output: dict
    object_keys: list[Text]
    execution_status: Literal["completed", "failed", "skipped"]
    failure: Failure | None

    @model_validator(mode="after")
    def check_output(self):
        if self.execution_status == "completed":
            validated = OUTPUT_TYPES[self.operation].model_validate(self.output)
            object.__setattr__(self, "output", validated.model_dump(mode="json"))
        elif self.output:
            raise ValueError("Failed or skipped analysis cannot carry computed output")
        return self


class Statement(Record):
    statement_id: Text
    text: Text
    kind: Literal["factual", "hypothesis", "recommendation", "limitation"]


class CandidateQuestion(Record):
    question: Text
    hypothesis: Text
    resources: Text
    novelty_risk: Text
    feasibility: Text
    statement_ids: NonemptyTexts


class IdeaPayload(Record):
    task_type: Literal["idea_exploration"]
    candidate_questions: Annotated[list[CandidateQuestion], Field(min_length=1)]
    recommendation: Text
    minimal_validation: Text


class MethodRow(Record):
    work: Text
    input_representation: Text
    mechanism: Text
    output: Text
    solved_limits: Text
    open_problems: Text
    statement_ids: NonemptyTexts


class MethodPayload(Record):
    task_type: Literal["method_differentiation"]
    comparison_rows: Annotated[list[MethodRow], Field(min_length=1)]
    differential_claims: NonemptyTexts
    contribution_boundary: Text


class ProtocolRow(Record):
    claim_id: Text
    protocol: Text
    controls: NonemptyTexts
    metrics: NonemptyTexts
    supported_conclusions: Text
    unsupported_conclusions: Text
    statement_ids: NonemptyTexts


class EvaluationPayload(Record):
    task_type: Literal["evaluation_design"]
    protocol_rows: Annotated[list[ProtocolRow], Field(min_length=1)]
    failure_modes: NonemptyTexts


TaskPayload = Annotated[
    IdeaPayload | MethodPayload | EvaluationPayload, Field(discriminator="task_type")
]


class DraftSection(Record):
    section_id: SectionID
    title: Text
    content: Text
    draft_version: Positive
    statements: list[Statement]
    task_payload: TaskPayload | None


class DraftClaimBinding(Record):
    draft_version: Positive
    section_id: SectionID
    statement_id: Text
    claim_ids: list[Text]
    cited_evidence_ids: list[Text]
    artifact_ids: list[Text]


class CriticFeedback(Record):
    issue_id: Text
    draft_version: Positive
    target_type: Literal[
        "source",
        "evidence",
        "claim",
        "metric",
        "comparison_set",
        "artifact",
        "draft_section",
        "statement",
    ]
    target_id: Text
    section_id: SectionID
    issue_type: IssueType
    severity: Literal["critical", "major", "minor"]
    fillable: StrictBool
    description: Text
    resolved: StrictBool
    resolution: Text | None
    resolved_in_version: Positive | None


class ReworkTarget(Record):
    issue_ids: list[Text]
    section_ids: list[SectionID]
    claim_ids: list[Text]
    action: Literal["re_research", "re_analyze", "revise", "acknowledge_limit"]
    reason: Text


class Reference(Record):
    reference_id: Text
    source_id: Text
    title: Text
    canonical_url: Text | None
    version: Text | None
    locations: list[Location]
    evidence_ids: list[Text]


class RiskItem(Record):
    risk_id: Text
    description: Text
    evidence_status: Text
    impact: Text
    verification_action: Text
    claim_ids: list[Text]
    issue_ids: list[Text]


class FinalReport(Record):
    report_id: UUID
    session_id: UUID
    run_id: UUID
    version: Positive
    brief_version: Positive
    draft_version: Positive
    review_verdict: ReviewVerdict
    title: Text
    markdown: Text
    sections: dict[SectionID, DraftSection]
    bindings: list[DraftClaimBinding]
    references: list[Reference]
    risks: list[RiskItem]
    created_at: UTC
