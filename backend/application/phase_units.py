"""Trusted unit scopes and immutable completion envelopes; not model controls."""

from typing import Literal

from pydantic import Field, JsonValue, StrictBool, model_validator

from application.records import ClaimedRun
from domain.research.facts import SectionID
from domain.research.ids import canonical_hash, stable_id
from domain.research.machine import PipelineDecision
from domain.research.models import Hash, Record, Text
from domain.research.phase_contracts import PhaseResult, WorkerPhase
from domain.research.state import Checkpoint, PipelineState


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


class PhaseTransition(Record):
    claimed: ClaimedRun
    checkpoint: Checkpoint
    decision: PipelineDecision


def plan_units(state: PipelineState) -> list[UnitScope]:
    """Stable pending phase identities; outputs/budgets/timestamps never enter IDs.

    Retrieval queries have separate checkpoints, followed by chapter coverage.
    Bounded gap filling/tracing can schedule additional explicit scopes through
    the same coordinator; it must not hide work in an uncheckpointed phase loop.
    """
    state = PipelineState.model_validate(state)
    if state.phase == "done":
        raise ValueError("A published state has no phase units")
    sections = sorted(
        {section for target in state.run_metadata.rework_targets for section in target.section_ids}
    )
    if not sections or state.phase in {"plan", "review"}:
        sections = [f"section_{n}" for n in range(1, 6)]
    generation = {
        "round": state.run_metadata.rework_count,
        "stop_reason": state.run_metadata.stop_reason,
    }

    def unit(section_ids, parameters, requirements=()):
        identifier = stable_id(
            "unit",
            str(state.run_id),
            state.phase,
            generation,
            section_ids,
            list(requirements),
            parameters,
        )
        return UnitScope(
            unit_id=identifier,
            phase=state.phase,
            section_ids=section_ids,
            requirement_ids=list(requirements),
            parameters=parameters,
        )

    if state.phase in {"plan", "write", "review"}:
        return [unit(sections, {"kind": state.phase})]
    units = []
    if state.phase == "research":
        if state.run_metadata.stop_reason in {
            "rework_limit",
            "budget_exhausted",
            "deadline_exhausted",
        }:
            raise ValueError("Terminal contraction cannot plan research units")
        for plan in state.section_plans:
            if plan.section_id not in sections:
                continue
            questions = dict.fromkeys(question.strip() for question in plan.sub_questions)
            units.extend(
                unit([plan.section_id], {"kind": "query", "query": question})
                for question in questions
            )
            units.append(unit([plan.section_id], {"kind": "coverage"}))
    else:
        if state.run_metadata.stop_reason in {
            "rework_limit",
            "budget_exhausted",
            "deadline_exhausted",
        }:
            raise ValueError("Terminal contraction cannot plan analysis units")
        for plan in state.section_plans:
            if plan.section_id not in sections:
                continue
            units.extend(
                unit([plan.section_id], {"kind": "analysis"}, [requirement.requirement_id])
                for requirement in plan.analysis_requirements
            )
        if not units:
            units.append(
                unit(
                    sections,
                    {
                        "kind": "analysis_skip",
                        "reason": "No quantitative analysis requirement in target sections",
                    },
                )
            )
    if not units:
        raise ValueError("A phase must contain at least one verifiable unit")
    return units
