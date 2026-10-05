"""Typed application aggregates for atomic repository operations."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from domain.research.ids import canonical_hash
from domain.research.models import (
    UTC,
    BriefRecord,
    Hash,
    Message,
    Positive,
    Record,
    ResearchBrief,
    ResearchRun,
    SessionState,
    SourceSelection,
    Text,
)
from domain.research.state import Checkpoint


class User(Record):
    user_id: UUID
    email: Annotated[Text, Field(min_length=3, max_length=254)]
    password_hash: Text | None = Field(repr=False)
    is_development: StrictBool
    created_at: UTC

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return value.lower()

    @model_validator(mode="after")
    def development_cannot_login(self):
        if self.is_development and self.password_hash is not None:
            raise ValueError("Development user must not have login credentials")
        if not self.is_development and self.password_hash is None:
            raise ValueError("Registered user requires password hash")
        return self


class SessionChange(Record):
    session: SessionState
    messages: list[Message]
    brief: BriefRecord

    @model_validator(mode="after")
    def coherent_candidate(self):
        session, brief = self.session, self.brief
        if (
            brief.session_id != session.session_id
            or brief.version != session.brief_version
            or brief.source_selection != session.source_selection
        ):
            raise ValueError("Session and brief identity differ")
        if brief.frozen_at is not None or session.run_id is not None:
            raise ValueError("Freeze must use the atomic freeze operation")
        if brief.content.model_dump(exclude_unset=True) != session.brief_draft.model_dump(
            exclude_unset=True
        ):
            raise ValueError("Current brief content differs from draft")
        if any(
            message.session_id != session.session_id
            or message.brief_version != session.brief_version
            for message in self.messages
        ):
            raise ValueError("Messages do not belong to candidate version")
        if len({message.sequence for message in self.messages}) != len(self.messages):
            raise ValueError("Duplicate message sequence")
        if len({message.message_id for message in self.messages}) != len(self.messages):
            raise ValueError("Duplicate message identity")
        return self


class SessionInput(Record):
    session: SessionState
    history: tuple[Message, ...]

    @model_validator(mode="after")
    def history_belongs_to_session(self):
        if any(
            message.session_id != self.session.session_id
            or message.brief_version > self.session.brief_version
            for message in self.history
        ):
            raise ValueError("History belongs to a different session or future brief")
        sequences = [message.sequence for message in self.history]
        if sequences != sorted(set(sequences)):
            raise ValueError("History must be ordered and duplicate-free")
        return self


class ValidatedFrozenInput(Record):
    owner_id: UUID
    session_id: UUID
    expected_revision: Positive
    expected_brief_version: Positive
    research_brief: ResearchBrief
    source_selection: SourceSelection
    confirmed_by: UUID
    origin: Literal["http", "cli"]

    @model_validator(mode="after")
    def confirmation_has_owner(self):
        if self.confirmed_by != self.owner_id:
            raise ValueError("Confirmation identity differs from owner")
        return self


class FreezeCommit(Record):
    expected_revision: Positive
    session: SessionState
    brief: BriefRecord
    run: ResearchRun
    checkpoint: Checkpoint
    messages: list[Message]

    @model_validator(mode="after")
    def coherent_freeze(self):
        session, brief, run, checkpoint = self.session, self.brief, self.run, self.checkpoint
        if session.revision != self.expected_revision + 1 or session.status != "ready":
            raise ValueError("Freeze requires next session revision and ready state")
        if (
            session.session_id != brief.session_id
            or session.session_id != run.session_id
            or session.run_id != run.run_id
            or run.run_id != checkpoint.run_id
        ):
            raise ValueError("Freeze resource identity mismatch")
        if (
            brief.frozen_at is None
            or brief.confirmed_by != session.owner_id
            or brief.version != session.brief_version
            or brief.version != run.brief_version
            or brief.content_hash != run.brief_hash
        ):
            raise ValueError("Freeze confirmation/version/hash mismatch")
        if session.brief_draft.model_dump() != brief.content.model_dump():
            raise ValueError("Frozen session draft differs from confirmed content")
        if (
            run.status != "ready"
            or run.phase != "plan"
            or run.checkpoint_seq != 1
            or checkpoint.seq != 1
            or checkpoint.phase != "plan"
        ):
            raise ValueError("New run must have the initial checkpoint")
        if (
            run.attempt_count != 0
            or run.lease_token != 0
            or run.lease_owner is not None
            or run.lease_expires_at is not None
            or run.started_at is not None
            or run.finished_at is not None
            or run.cancel_requested_at is not None
        ):
            raise ValueError("New run cannot have an execution or cancellation history")
        state = checkpoint.state
        if (
            state.session_id != session.session_id
            or state.brief_version != brief.version
            or state.brief_hash != brief.content_hash
            or state.research_brief != brief.content
            or state.run_metadata.config != run.config_snapshot
            or state.source_selection != session.source_selection
            or brief.source_selection != session.source_selection
        ):
            raise ValueError("Freeze snapshot differs from confirmed input")
        if any(
            message.session_id != session.session_id or message.brief_version != brief.version
            for message in self.messages
        ):
            raise ValueError("Confirmation message belongs to a different session/version")
        if len({message.message_id for message in self.messages}) != len(self.messages):
            raise ValueError("Duplicate confirmation message identity")
        return self


class IdempotencyRecord(Record):
    owner_id: UUID
    operation: Text
    key: Annotated[Text, Field(max_length=128)]
    request_hash: Hash
    state: Literal["in_progress", "completed"]
    lease_expires_at: UTC
    response_status: Annotated[Positive, Field(ge=100, le=599)] | None
    response_body: dict | None
    resource_id: UUID | None

    @model_validator(mode="after")
    def check_response(self):
        if self.state == "completed" and self.response_status is None:
            raise ValueError("Completed request requires cached HTTP status")
        if self.state == "in_progress" and (
            self.response_status is not None or self.response_body is not None
        ):
            raise ValueError("In-progress request cannot contain completed response")
        canonical_hash(self.response_body)
        return self
