"""Shared execute_phase boundary for orchestration and read-only CLI debugging."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from application.errors import AppError
from application.phase_units import UnitScope
from domain.research.ids import canonical_hash
from domain.research.models import UTC as AwareUTC
from domain.research.models import Hash, Positive, Record, RunConfig, Text
from domain.research.phase_contracts import READS, PhaseInput, PhaseResult

CancelCheck = Callable[[], Awaitable[bool]]
ToolInvoke = Callable[[str, dict], Awaitable[Any]]
DiagnosticEmit = Callable[[Any], None]


class ExecutionContext(Record):
    """Coordinator-owned capabilities; never serialize this object as a State."""

    owner_id: UUID
    run_id: UUID
    config: RunConfig
    brief_hash: Hash
    lease_token: Positive
    unit_id: Text
    deadline: AwareUTC
    cancel_check: CancelCheck
    invoke: ToolInvoke
    emit: DiagnosticEmit
    unit: UnitScope | None = None


@dataclass(frozen=True)
class WorkerContext:
    """Only scoped tools/config/stop checks; no repository, owner or lease handle."""

    config: RunConfig
    unit_id: str
    deadline: datetime
    cancel_check: CancelCheck
    invoke: ToolInvoke
    emit: DiagnosticEmit
    unit: UnitScope | None = None


PhaseWorker = Callable[[PhaseInput, WorkerContext], Awaitable[PhaseResult]]


class PhaseExecutor:
    def __init__(self, workers: Mapping[str, PhaseWorker]):
        if not set(workers) <= set(READS) or any(
            not callable(worker) for worker in workers.values()
        ):
            raise ValueError("Only explicitly registered canonical phase workers are allowed")
        self.workers = dict(workers)

    async def execute_phase(self, value: PhaseInput, context: ExecutionContext) -> PhaseResult:
        value = PhaseInput.model_validate(value)
        context = ExecutionContext.model_validate(context)
        if context.unit is not None and (
            context.unit.unit_id != context.unit_id or context.unit.phase != value.phase
        ):
            raise AppError("invalid_state", "Unit scope differs from execution context")
        selection = value.values["source_selection"]
        if (
            canonical_hash(value.values["research_brief"]) != context.brief_hash
            or selection.categories != context.config.source_policy.categories
            or selection.knowledge_base_ids != context.config.source_policy.knowledge_base_ids
        ):
            raise AppError("invalid_state", "Phase input differs from the frozen execution scope")
        worker = self.workers.get(value.phase)
        if worker is None:
            raise AppError("service_not_ready", "Requested phase worker is not configured")
        if context.deadline <= datetime.now(UTC):
            raise AppError("invalid_session_state", "Execution deadline is exhausted")
        if await context.cancel_check():
            raise AppError("invalid_session_state", "Execution is stopping")
        semantic_hash = value.semantic_hash
        # Revalidate JSON clone: frozen models can still contain mutable nested
        # dict/list values. Worker mutation must never alter the owned State.
        received = PhaseInput.model_validate_json(value.model_dump_json())
        tools = WorkerContext(
            config=RunConfig.model_validate_json(context.config.model_dump_json()),
            unit_id=context.unit_id,
            deadline=context.deadline,
            cancel_check=context.cancel_check,
            invoke=context.invoke,
            emit=context.emit,
            unit=UnitScope.model_validate_json(context.unit.model_dump_json())
            if context.unit is not None
            else None,
        )
        raw = await worker(received, tools)
        try:
            if (
                received.semantic_hash != semantic_hash
                or tools.config != context.config
                or tools.unit != context.unit
            ):
                raise ValueError("Worker modified its input")
            result = PhaseResult.model_validate(raw)
            if (
                result.phase != value.phase
                or result.unit_id != context.unit_id
                or result.input_hash != semantic_hash
            ):
                raise ValueError("Worker result has a different execution identity")
        except (ValueError, TypeError, ValidationError):
            raise AppError(
                "invalid_state", "Worker result violates phase authority or execution identity"
            ) from None
        # No post-call cancellation rejection: an I/O unit that has completed
        # may be safely checkpointed before the coordinator stops the Run.
        return result
