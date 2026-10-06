"""Semantic call identity and persisted tool record, independent of attempts."""

from typing import Any, Literal
from uuid import UUID

from pydantic import model_validator

from domain.research.ids import canonical_hash
from domain.research.models import UTC, Failure, Hash, Nonnegative, Record, SourcePolicy, Text
from domain.research.state import VersionReference


class ToolCallIdentity(Record):
    run_id: UUID
    tool: Literal["llm", "search", "fetch", "analysis"]
    provider: Text
    version: Text
    arguments: dict[str, Any]
    input_hash: Hash
    source_policy: SourcePolicy
    knowledge_snapshot: list[VersionReference]

    @model_validator(mode="after")
    def validate_identity(self):
        try:
            canonical_hash(self.arguments)  # Reject NaN/non-JSON before any I/O.
        except (ValueError, TypeError):
            raise ValueError("Call arguments must be finite JSON data") from None
        if any(
            v.kb_id not in self.source_policy.knowledge_base_ids for v in self.knowledge_snapshot
        ):
            raise ValueError("Call knowledge scope exceeds frozen policy")
        if len({canonical_hash(v) for v in self.knowledge_snapshot}) != len(
            self.knowledge_snapshot
        ):
            raise ValueError("Duplicate knowledge version in call identity")
        return self

    @property
    def call_key(self) -> str:
        value = self.model_dump(mode="json")
        # Scope is a set, not UI selection order. Query/body text is deliberately
        # NOT normalized here: semantically relevant whitespace cannot collide.
        value["source_policy"]["categories"].sort()
        value["source_policy"]["knowledge_base_ids"].sort()
        value["knowledge_snapshot"].sort(key=canonical_hash)
        return canonical_hash(value)

    @property
    def call_id(self) -> str:
        return "tool_" + self.call_key


class ToolCallRecord(Record):
    call_id: Text
    run_id: UUID
    call_key: Hash
    status: Literal["reserved", "succeeded", "failed", "uncertain"]
    request_hash: Hash
    result_object_key: Text | None
    result_hash: Hash | None
    failure: Failure | None
    budget_units: Nonnegative
    created_at: UTC
    updated_at: UTC

    @model_validator(mode="after")
    def validate_result(self):
        if self.updated_at < self.created_at:
            raise ValueError("Call timestamps are reversed")
        if self.status == "succeeded" and (
            self.result_object_key is None or self.result_hash is None or self.failure is not None
        ):
            raise ValueError("Successful call requires immutable result and no failure")
        if (self.result_object_key is None) != (self.result_hash is None):
            raise ValueError("Result key and hash must be paired")
        return self
