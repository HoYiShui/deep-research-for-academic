"""Mono knowledge lifecycle and retrieval records, without I/O or Agent policy."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, model_validator

from domain.research.facts import Location
from domain.research.ids import canonical_hash
from domain.research.models import UTC, Failure, Hash, Nonnegative, Positive, Record, Text

Classification = Literal["public", "private"]
ChunkType = Literal["text", "table", "formula"]
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class LeasedRecord(Record):
    lease_owner: Text | None
    lease_token: Nonnegative
    lease_expires_at: UTC | None

    @model_validator(mode="after")
    def check_lease(self):
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("Lease owner and expiry must be paired")
        if self.lease_owner is not None and self.lease_token == 0:
            raise ValueError("A held lease requires a positive fencing token")
        return self


class KnowledgeBase(LeasedRecord):
    kb_id: UUID
    owner_id: UUID
    name: Annotated[Text, Field(max_length=100)]
    description: Annotated[StrictStr, Field(max_length=2000)] | None
    data_classification: Classification = "private"
    status: Literal["creating", "active", "deleting", "deleted"]
    revision: Positive
    index_version: Text
    cleanup_cursor: Text | None
    failure: Failure | None
    created_at: UTC
    updated_at: UTC


class KnowledgeBasePatch(Record):
    revision: Positive
    name: Annotated[Text, Field(max_length=100)] | None = None
    description: Annotated[StrictStr, Field(max_length=2000)] | None = None

    @model_validator(mode="after")
    def changes(self):
        if not self.model_fields_set.intersection({"name", "description"}):
            raise ValueError("At least one editable field is required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null")
        return self


class KnowledgeBaseCreate(Record):
    name: Annotated[Text, Field(max_length=100)]
    description: Annotated[StrictStr, Field(max_length=2000)] | None = None
    data_classification: Classification = "private"


class KnowledgeBaseView(Record):
    kb_id: UUID
    name: Annotated[Text, Field(max_length=100)]
    description: Annotated[StrictStr, Field(max_length=2000)] | None
    data_classification: Classification
    status: Literal["creating", "active", "deleting", "deleted"]
    revision: Positive
    index_version: Text
    failure: dict | None
    created_at: UTC
    updated_at: UTC


class Document(LeasedRecord):
    document_id: UUID
    kb_id: UUID
    filename: Text
    media_type: Literal["application/pdf"]
    status: Literal["active", "deleting", "deleted"]
    active_version_id: UUID | None
    revision: Positive
    cleanup_cursor: Text | None
    failure: Failure | None
    created_at: UTC
    updated_at: UTC


class DocumentVersion(Record):
    document_version_id: UUID
    document_id: UUID
    kb_id: UUID
    content_hash: Hash
    ingestion_version: Text
    index_version: Text
    source_object_key: Text
    parsed_object_key: Text | None
    manifest_object_key: Text | None
    chunk_count: Annotated[Nonnegative, Field(le=10000)]
    status: Literal["staging", "active", "failed", "retired"]
    created_at: UTC
    activated_at: UTC | None

    @model_validator(mode="after")
    def published(self):
        if self.status in {"active", "retired"} and (
            self.activated_at is None or self.chunk_count == 0
        ):
            raise ValueError("Published versions require chunks and activation time")
        return self


class IngestionProgress(Record):
    step: Literal["upload", "parse", "chunk", "embed", "index", "commit", "cleanup"]
    completed_units: Nonnegative
    total_units: Nonnegative | None
    completed_batches: list[Text]

    @model_validator(mode="after")
    def bounded(self):
        if self.total_units is not None and self.completed_units > self.total_units:
            raise ValueError("Progress exceeds total")
        if len(set(self.completed_batches)) != len(self.completed_batches):
            raise ValueError("Duplicate completed batch identity")
        return self


class JobAttempt(Record):
    attempt: Annotated[Positive, Field(le=3)]
    started_at: UTC
    finished_at: UTC | None
    status: Literal["processing", "completed", "failed", "cancelled"]
    failure: Failure | None
    progress: IngestionProgress

    @model_validator(mode="after")
    def terminal(self):
        if (self.status == "processing") != (self.finished_at is None):
            raise ValueError("Attempt finish time differs from its status")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("Attempt finishes before it starts")
        return self


class IngestionJob(LeasedRecord):
    job_id: UUID
    document_version_id: UUID
    idempotency_key: Annotated[Text, Field(max_length=128)]
    status: Literal["accepted", "processing", "cancelling", "completed", "failed", "cancelled"]
    attempt_count: Annotated[Nonnegative, Field(le=3)]
    attempt_history: list[JobAttempt]
    progress: IngestionProgress
    cancel_requested_at: UTC | None
    failure: Failure | None
    created_at: UTC
    started_at: UTC | None
    finished_at: UTC | None

    @model_validator(mode="after")
    def history(self):
        if [item.attempt for item in self.attempt_history] != list(
            range(1, self.attempt_count + 1)
        ):
            raise ValueError("Attempt history must preserve every numbered attempt")
        if self.status == "processing" and (
            self.lease_owner is None
            or not self.attempt_history
            or self.attempt_history[-1].status != "processing"
        ):
            raise ValueError("Processing requires a held lease and active attempt")
        if self.status in {"completed", "failed", "cancelled"} and (
            self.finished_at is None or self.lease_owner is not None
        ):
            raise ValueError("Terminal job must be finished and unleased")
        return self


class Chunk(Record):
    chunk_id: Text
    kb_id: UUID
    document_id: UUID
    document_version_id: UUID
    ordinal: Annotated[Nonnegative, Field(lt=10000)]
    chunk_type: ChunkType
    content_object_key: Text
    content_hash: Hash
    embedding_anchor: Text
    location: Location
    metadata: dict

    @model_validator(mode="after")
    def finite_metadata(self):
        canonical_hash(self.metadata)
        return self


class IngestionJobContext(Record):
    kb: KnowledgeBase
    document: Document
    version: DocumentVersion
    job: IngestionJob

    @model_validator(mode="after")
    def ownership_chain(self):
        if (
            self.kb.kb_id != self.document.kb_id
            or self.kb.kb_id != self.version.kb_id
            or self.document.document_id != self.version.document_id
            or self.job.document_version_id != self.version.document_version_id
        ):
            raise ValueError("Job context has mismatched parents")
        return self


class JobAccepted(Record):
    job_id: UUID
    status: Literal["accepted"]


class SourceMetadata(Record):
    title: Text
    filename: Text
    year: Annotated[Positive, Field(ge=1900, le=2100)] | None
    data_classification: Classification
    content_hash: Hash


class RetrievalTrace(Record):
    dense_rank: Positive | None
    sparse_rank: Positive | None
    fused_score: Finite
    rerank_score: Finite | None
    index_version: Text
    degraded: StrictBool


class RetrievalResult(Record):
    kb_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: Text
    content: Text
    score: Finite
    location: Location
    source_metadata: SourceMetadata
    retrieval_trace: RetrievalTrace


class VectorHit(Record):
    chunk_id: Text
    dense_rank: Positive | None
    sparse_rank: Positive | None
    fused_score: Finite


class RankedHit(Record):
    chunk_id: Text
    score: Finite
