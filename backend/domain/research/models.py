"""Strict mono-v1 research inputs and persisted lifecycle records.

No I/O or transitions live here. Services construct validated candidates and
repositories commit them atomically. Legacy agent fact types are migrated in
the following tasks; they are not valid frozen input records.
"""

from __future__ import annotations

from datetime import UTC as datetime_utc
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from domain.research.ids import canonical_hash

Text = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1)]
BriefText = Annotated[Text, Field(max_length=8000)]
Assumptions = Annotated[StrictStr, StringConstraints(strip_whitespace=True, max_length=8000)]
Positive = Annotated[StrictInt, Field(gt=0)]
Nonnegative = Annotated[StrictInt, Field(ge=0)]
Hash = Annotated[StrictStr, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
UTC = Annotated[AwareDatetime, AfterValidator(lambda v: v.astimezone(datetime_utc))]
TaskType = Literal["idea_exploration", "method_differentiation", "evaluation_design"]
SourceCategory = Literal["papers", "web", "knowledge_base"]
SessionStatus = Literal[
    "ask", "confirm", "ready", "running", "cancelling", "completed", "failed", "cancelled"
]
RunStatus = Literal["ready", "running", "cancelling", "completed", "failed", "cancelled"]
ResearchPhase = Literal["plan", "research", "analyze", "write", "review", "done"]
ReviewVerdict = Literal["approved", "approved_with_risks", "needs_more_work"]
BriefField = Literal[
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
]
Operation = Literal["comparison_matrix", "pairwise_delta", "plot", "statistic", "aggregation"]
AgentMethod = Literal["clarify", "plan", "research", "analyze", "write", "review"]


class Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class ResearchBrief(Record):
    task_type: TaskType
    decision_goal: BriefText
    research_object: BriefText
    scope: BriefText
    comparison_scope: BriefText
    claims_to_verify: BriefText
    evidence_requirements: BriefText
    conclusion_boundary: BriefText
    deliverable: BriefText
    assumptions: Assumptions


class PartialResearchBrief(Record):
    """Omission is legal; explicit null, wrong types, and blank values are not."""

    task_type: TaskType | None = None
    decision_goal: BriefText | None = None
    research_object: BriefText | None = None
    scope: BriefText | None = None
    comparison_scope: BriefText | None = None
    claims_to_verify: BriefText | None = None
    evidence_requirements: BriefText | None = None
    conclusion_boundary: BriefText | None = None
    deliverable: BriefText | None = None
    assumptions: Assumptions | None = None

    @model_validator(mode="wrap")
    @classmethod
    def preserve_omission_on_revalidation(cls, data, handler):
        if isinstance(data, cls):
            data = data.model_dump(exclude_unset=True)
        return handler(data)

    @model_serializer(mode="wrap")
    def serialize_present_fields(self, handler):
        """Persist the subset, never emit null placeholders for absent fields."""
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}

    @model_validator(mode="before")
    @classmethod
    def reject_null(cls, data):
        if isinstance(data, dict) and any(value is None for value in data.values()):
            raise ValueError("Brief fields must be omitted, not null")
        return data


class SourceSelection(Record):
    categories: Annotated[list[SourceCategory], Field(min_length=1)] = Field(
        default_factory=lambda: ["papers", "web"]
    )
    knowledge_base_ids: Annotated[list[UUID], Field(max_length=10)] = Field(default_factory=list)

    @field_validator("categories", "knowledge_base_ids", mode="after")
    @classmethod
    def deduplicate(cls, values):
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def check_knowledge_selection(self):
        if ("knowledge_base" in self.categories) != bool(self.knowledge_base_ids):
            raise ValueError("Knowledge base category and IDs must be selected together")
        return self


class ClarifyAssessment(Record):
    missing_fields: list[BriefField]
    questions: Annotated[list[Text], Field(max_length=2)]
    brief_patch: PartialResearchBrief
    assumptions: list[Text]
    field_reasons: dict[BriefField, Text]

    @field_validator("missing_fields", "questions", "assumptions")
    @classmethod
    def deduplicate(cls, values):
        return list(dict.fromkeys(values))


class Failure(Record):
    code: Text
    dependency: Text | None
    operation: Text
    phase: ResearchPhase | None
    message: Text
    retryable: StrictBool
    resume_allowed: StrictBool
    attempt: Nonnegative
    occurred_at: UTC
    details: dict | None

    @field_validator("details")
    @classmethod
    def scalar_diagnostics(cls, data):
        if data is None:
            return data
        for key, value in data.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Diagnostic keys must be nonempty strings")
            values = value if isinstance(value, list) else [value]
            if any(type(item) not in (str, int, float, bool, type(None)) for item in values):
                raise ValueError("Diagnostics accept only JSON scalars or scalar lists")
        canonical_hash(data)  # Reject NaN/Infinity.
        return data


class SessionState(Record):
    session_id: UUID
    owner_id: UUID
    query: Annotated[Text, Field(max_length=16000)]
    status: SessionStatus
    revision: Positive
    brief_draft: PartialResearchBrief
    brief_version: Positive
    pending_questions: Annotated[list[Text], Field(max_length=2)]
    missing_fields: list[BriefField]
    clarification_round: Nonnegative
    clarification_limit_reached: StrictBool
    source_selection: SourceSelection
    run_id: UUID | None
    failure: Failure | None
    created_at: UTC
    updated_at: UTC


class Message(Record):
    message_id: UUID
    session_id: UUID
    sequence: Positive
    role: Literal["user", "assistant", "system"]
    kind: Literal["initial", "answer", "assessment", "confirmation", "rejection"]
    content: Text
    assessment: ClarifyAssessment | None
    brief_version: Positive
    created_at: UTC


class BriefRecord(Record):
    session_id: UUID
    version: Positive
    content: ResearchBrief | PartialResearchBrief
    frozen_at: UTC | None
    confirmed_by: UUID | None
    content_hash: Hash | None
    source_selection: SourceSelection

    @model_validator(mode="after")
    def check_freeze(self):
        if self.frozen_at is None:
            if self.confirmed_by is not None or self.content_hash is not None:
                raise ValueError("An unfrozen draft cannot have confirmation or frozen hash")
        else:
            if self.confirmed_by is None or self.content_hash is None:
                raise ValueError("A frozen brief requires confirmation and hash")
            brief = ResearchBrief.model_validate(self.content.model_dump(exclude_unset=True))
            if canonical_hash(brief) != self.content_hash:
                raise ValueError("Frozen brief hash mismatch")
            object.__setattr__(self, "content", brief)
        return self


class Versions(Record):
    llm_provider: Text
    llm_model: Text
    llm_revision: Text
    prompt_versions: dict[AgentMethod, Text]
    template_versions: dict[Operation, Text]
    parser_version: Text
    chunker_version: Text
    embedding_version: Text
    reranker_version: Text
    index_version: Text

    @model_validator(mode="after")
    def require_all_versions(self):
        if set(self.prompt_versions) != {
            "clarify",
            "plan",
            "research",
            "analyze",
            "write",
            "review",
        }:
            raise ValueError("All agent prompt versions must be pinned")
        if set(self.template_versions) != {
            "comparison_matrix",
            "pairwise_delta",
            "plot",
            "statistic",
            "aggregation",
        }:
            raise ValueError("All operation template versions must be pinned")
        return self


class SourcePolicy(SourceSelection):
    private_only: StrictBool

    @model_validator(mode="after")
    def check_private_sources(self):
        if self.private_only and self.categories != ["knowledge_base"]:
            raise ValueError("Private-only policy cannot use external source categories")
        return self


class RunLimits(Record):
    deadline_s: Positive
    search_calls: Positive
    fetch_calls: Positive
    llm_calls: Positive
    tokens: Positive
    terminal_reserved_calls: Positive
    terminal_reserved_tokens: Positive
    rework_rounds: Annotated[Nonnegative, Field(le=3)]
    citation_depth: Annotated[Positive, Field(le=2)]
    gap_queries_per_spec: Annotated[Positive, Field(le=2)]

    @model_validator(mode="after")
    def reserve_within_budget(self):
        if (
            self.terminal_reserved_calls >= self.llm_calls
            or self.terminal_reserved_tokens >= self.tokens
        ):
            raise ValueError("Terminal reserve must leave a research budget")
        return self


class Timeouts(Record):
    llm: Positive
    search: Positive
    fetch: Positive
    embedding: Positive
    rerank: Positive
    vector: Positive
    content: Positive
    parser: Positive
    sandbox: Positive


class Concurrency(Record):
    search: Positive
    fetch: Positive
    llm: Positive
    local_inference: Positive


class RunConfig(Record):
    versions: Versions
    source_policy: SourcePolicy
    limits: RunLimits
    timeouts_s: Timeouts
    concurrency: Concurrency


class ResearchRun(Record):
    run_id: UUID
    session_id: UUID
    brief_version: Positive
    brief_hash: Hash
    status: RunStatus
    phase: ResearchPhase
    attempt_count: Nonnegative
    checkpoint_seq: Positive
    cancel_requested_at: UTC | None
    lease_owner: Text | None
    lease_token: Nonnegative
    lease_expires_at: UTC | None
    resume_allowed: StrictBool
    failure: Failure | None
    config_snapshot: RunConfig
    created_at: UTC
    started_at: UTC | None
    finished_at: UTC | None
