"""Trusted unit scopes and immutable completion envelopes; not model controls."""

from typing import Literal

from pydantic import Field, JsonValue, StrictBool, model_validator

from application.records import ClaimedRun
from domain.research.facts import SectionID
from domain.research.ids import canonical_hash
from domain.research.models import Hash, Record, Text
from domain.research.phase_contracts import PhaseResult, WorkerPhase
from domain.research.state import Checkpoint


class UnitScope(Record):
    unit_id: Text
    phase: WorkerPhase
    section_ids: list[SectionID] = Field(min_length=1)
    requirement_ids: list[Text]
    parameters: dict[str, JsonValue]

    @model_validator(mode="after")
    def unique_scope(self):
        canonical_hash(self.parameters)
        if len(set(self.section_ids)) != len(self.section_ids) or len(
            set(self.requirement_ids)
        ) != len(self.requirement_ids):
            raise ValueError("Unit scopes cannot contain duplicate targets")
        if self.requirement_ids and self.phase != "analyze":
            raise ValueError("Only analysis units can narrow requirement scope")
        return self


class UnitEnvelope(Record):
    schema_version: Literal[1]
    scope: UnitScope
    input_hash: Hash
    result: PhaseResult

    @model_validator(mode="after")
    def matching_identity(self):
        if (
            self.scope.phase != self.result.phase
            or self.scope.unit_id != self.result.unit_id
            or self.input_hash != self.result.input_hash
        ):
            raise ValueError("Unit envelope has mismatched execution identities")
        return self


class UnitCommit(Record):
    claimed: ClaimedRun
    checkpoint: Checkpoint
    skipped: StrictBool
