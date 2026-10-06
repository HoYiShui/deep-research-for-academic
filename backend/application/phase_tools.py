"""Coordinator binding from narrow worker callbacks to durable model calls.

Model and optional search/original-fetch bindings are explicit; analysis
adapters must be registered separately as those phase workers are implemented.
No legacy I/O or fake fallback is reachable through this capability.
"""

import asyncio
from dataclasses import dataclass

from application.errors import AppError
from application.fetch_tools import FetchBinding, FetchTools
from application.phase_units import UnitScope
from application.search_tools import SearchBinding, SearchTools
from application.tool_budget import ToolBudgetRequest
from application.tool_calls import ToolCallService, ToolOutput
from domain.model_completion import MeteredModelPort, ModelCompletion
from domain.ports import AdapterError
from domain.research.ids import canonical_hash
from domain.research.phase_contracts import PhaseInput
from domain.research.search import SearchBatch
from domain.research.state import VersionReference
from domain.research.tool_calls import ToolCallIdentity


@dataclass(frozen=True)
class ModelBinding:
    adapter: MeteredModelPort
    provider: str
    model: str
    revision: str
    token_reservation: int
    output_token_limit: int | None = None


class PhaseTools:
    """Run-owned binding; a Worker sees only the callback returned for its slice."""

    def __init__(
        self,
        service: ToolCallService,
        model: ModelBinding,
        knowledge_snapshot: list[VersionReference],
        *,
        model_slots: asyncio.Semaphore,
        search: SearchBinding | None = None,
        fetch: FetchBinding | None = None,
    ):
        config = service.claimed.run.config_snapshot
        versions = config.versions
        if (model.provider, model.model, model.revision) != (
            versions.llm_provider,
            versions.llm_model,
            versions.llm_revision,
        ):
            raise AppError("invalid_state", "Model adapter differs from frozen Run configuration")
        if type(model.token_reservation) is not int or model.token_reservation <= 0:
            raise ValueError("Model token reservation must be a positive integer")
        if model.output_token_limit is not None and (
            type(model.output_token_limit) is not int or model.output_token_limit <= 0
        ):
            raise ValueError("Model output token limit must be a positive integer")
        if config.source_policy.private_only and model.provider != "local":
            raise AppError(
                "privacy_policy_conflict", "Private scope cannot invoke an external model"
            )
        self._service, self._model = service, model
        self._knowledge = [
            VersionReference.model_validate_json(v.model_dump_json()) for v in knowledge_snapshot
        ]
        if not isinstance(model_slots, asyncio.Semaphore):
            raise TypeError("Composition root must supply its shared process model semaphore")
        self._slots = model_slots
        self._search = SearchTools(service, search, self._knowledge) if search is not None else None
        self._fetch = FetchTools(service, fetch, self._knowledge) if fetch is not None else None

    def for_phase(
        self,
        value: PhaseInput,
        *,
        terminal=False,
        allow_uncertain_replay=False,
        unit_scope: UnitScope | None = None,
    ):
        """Terminal reserve/replay authority is chosen by coordinator, not payload."""
        if type(terminal) is not bool or type(allow_uncertain_replay) is not bool:
            raise ValueError("Tool authority flags must be explicit booleans")
        value = PhaseInput.model_validate_json(value.model_dump_json())
        config = self._service.claimed.run.config_snapshot
        selection = value.values["source_selection"]
        if (
            canonical_hash(value.values["research_brief"]) != self._service.claimed.run.brief_hash
            or set(selection.categories) != set(config.source_policy.categories)
            or set(selection.knowledge_base_ids) != set(config.source_policy.knowledge_base_ids)
        ):
            raise AppError("invalid_state", "Tool slice differs from frozen Run scope")
        input_hash, phase = value.semantic_hash, value.phase
        if unit_scope is not None:
            unit_scope = UnitScope.model_validate_json(unit_scope.model_dump_json())
            if unit_scope.phase != phase:
                raise AppError("invalid_state", "Tool unit differs from requested phase")
            input_hash = canonical_hash(
                {"phase_input": input_hash, "unit_scope": unit_scope.model_dump(mode="json")}
            )
        model = self._model
        version = canonical_hash(
            {
                "model": model.model,
                "revision": model.revision,
                "prompt_version": config.versions.prompt_versions[phase],
                **(
                    {"output_token_limit": model.output_token_limit}
                    if model.output_token_limit is not None
                    else {}
                ),
            }
        )

        candidates = {}

        async def invoke(tool, arguments):
            if tool == "search" and self._search is not None:
                result = await self._search.invoke(
                    arguments,
                    phase=phase,
                    input_hash=input_hash,
                    unit=unit_scope,
                    allow_uncertain_replay=allow_uncertain_replay,
                )
                for item in SearchBatch.model_validate(result).items:
                    candidates[canonical_hash(item)] = item
                return result
            if tool == "fetch" and self._fetch is not None:
                return await self._fetch.invoke(
                    arguments,
                    phase=phase,
                    unit=unit_scope,
                    candidates=candidates,
                    allow_uncertain_replay=allow_uncertain_replay,
                )
            if tool != "llm":
                raise AppError("service_not_ready", "Requested phase tool is not configured")
            if (
                type(arguments) is not dict
                or set(arguments) != {"phase", "prompt"}
                or arguments["phase"] != phase
                or type(arguments["prompt"]) is not str
                or not arguments["prompt"].strip()
            ):
                raise AppError(
                    "invalid_state", "Model callback payload differs from phase authority"
                )
            # Make owned immutable-by-copy arguments before any await; workers
            # cannot change the prompt between its identity and the actual SDK request.
            prompt = arguments["prompt"]
            reservation = model.token_reservation
            if model.output_token_limit is not None:
                # UTF-8 byte count is a conservative input allowance, not a
                # claim of measured usage. Reserve each prompt, not the entire
                # remaining Run budget (which would prevent schema repair).
                reservation = len(prompt.encode("utf-8")) + 64 + model.output_token_limit
                if reservation > model.token_reservation:
                    raise AppError("budget_exhausted", "Model input allowance exceeds Run budget")
            identity = ToolCallIdentity(
                run_id=self._service.claimed.run.run_id,
                tool="llm",
                provider=model.provider,
                version=version,
                arguments={"phase": phase, "prompt": prompt},
                input_hash=input_hash,
                source_policy=config.source_policy,
                knowledge_snapshot=self._knowledge,
            )
            request = ToolBudgetRequest(
                tool="llm",
                token_reservation=reservation,
                terminal=terminal,
            )

            async def operation():
                completion = ModelCompletion.model_validate(
                    await model.adapter.complete_metered(prompt)
                )
                return ToolOutput(
                    content=completion.model_dump(mode="json"), tokens_used=completion.total_tokens
                )

            async with self._slots:
                result = await self._service.invoke(
                    identity,
                    request,
                    operation,
                    allow_uncertain_replay=allow_uncertain_replay,
                )
            # Reject truncated/wrong-model results only AFTER recording measured
            # usage. Schema repair uses a changed prompt and another paid attempt.
            try:
                completion = ModelCompletion.model_validate(result.content)
                if completion.total_tokens != result.tokens_used:
                    raise ValueError("Cached model usage differs")
                if completion.model != model.model or completion.stop_reason != "end_turn":
                    raise ValueError("Model completion is truncated or from another model")
            except (ValueError, TypeError):
                raise AdapterError(
                    "llm",
                    "model_output_invalid",
                    "Model completion cannot be used by this phase",
                    False,
                    phase,
                ) from None
            return completion.text

        return invoke
