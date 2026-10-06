"""Run-owned search authority: each provider request uses the durable ledger."""

from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

from application.errors import AppError
from application.ports import ResearchSearchPort
from application.tool_budget import ToolBudgetRequest
from application.tool_calls import ToolCallService, ToolOutput
from domain.ports import AdapterError
from domain.research.models import Record, Text
from domain.research.search import SearchBatch, SearchResult
from domain.research.tool_calls import ToolCallIdentity


class SearchProvider(Record):
    name: Text
    category: Literal["papers", "web"]
    revision: Text


@dataclass(frozen=True)
class SearchBinding:
    adapter: ResearchSearchPort
    providers: tuple[SearchProvider, ...]


class SearchTools:
    def __init__(self, service: ToolCallService, binding: SearchBinding, knowledge):
        self._service, self._adapter = service, binding.adapter
        providers = [SearchProvider.model_validate(provider) for provider in binding.providers]
        if not providers or len(providers) != len({provider.name for provider in providers}):
            raise ValueError("Search providers must be explicitly and uniquely bound")
        self._providers = {provider.name: provider for provider in providers}
        self._knowledge = list(knowledge)

    async def invoke(self, arguments, *, phase, input_hash, unit, allow_uncertain_replay):
        if (
            phase != "research"
            or self._service.claimed.run.phase != "research"
            or unit is None
            or unit.phase != "research"
            or unit.parameters.get("kind") != "query"
            or type(arguments) is not dict
            or set(arguments) != {"query"}
            or type(arguments["query"]) is not str
            or arguments["query"] != unit.parameters.get("query")
        ):
            raise AppError("invalid_state", "Search request differs from query unit authority")
        query = arguments["query"]
        config = self._service.claimed.run.config_snapshot
        categories = frozenset(config.source_policy.categories) & {"papers", "web"}
        if config.source_policy.private_only or not categories:
            raise AppError("privacy_policy_conflict", "Frozen scope forbids external search")

        async def attempt(name, received_query, ordinal, operation):
            provider = self._providers.get(name)
            if (
                provider is None
                or provider.category not in categories
                or received_query != query
                or type(ordinal) is not int
                or ordinal not in {1, 2}
            ):
                raise AppError("invalid_state", "Search adapter exceeded frozen provider authority")
            identity = ToolCallIdentity(
                run_id=self._service.claimed.run.run_id,
                tool="search",
                provider=provider.name,
                version=provider.revision,
                # Retries share semantic identity; PG owns the physical attempt ordinal.
                arguments={"query": query, "category": provider.category},
                input_hash=input_hash,
                source_policy=config.source_policy,
                knowledge_snapshot=self._knowledge,
            )

            def checked(raw):
                items = TypeAdapter(list[SearchResult]).validate_python(raw)
                expected = "paper" if provider.category == "papers" else "web"
                if len(items) > 50 or any(item.source_type != expected for item in items):
                    raise ValueError("Search result exceeds bound provider category or size")
                return items

            async def request():
                try:
                    items = checked(await operation())
                except (ValueError, TypeError):
                    raise AdapterError(
                        "search",
                        "search_response_invalid",
                        "Invalid search candidates",
                        False,
                        "search",
                    ) from None
                return ToolOutput(
                    content=[item.model_dump(mode="json") for item in items], tokens_used=0
                )

            result = await self._service.invoke(
                identity,
                ToolBudgetRequest(tool="search", token_reservation=0, terminal=False),
                request,
                # A timeout leaves a conservatively charged uncertain receipt.
                # Ordinal 2 follows only this invocation's retryable provider
                # failure; authorize that bounded read-only retry, never an old
                # lease's first attempt without explicit coordinator permission.
                allow_uncertain_replay=allow_uncertain_replay or ordinal == 2,
            )
            try:
                return checked(result.content)
            except (ValueError, TypeError):
                # Cache/storage failures must propagate, not become provider degradation.
                raise AdapterError(
                    "minio",
                    "content_invalid",
                    "Invalid cached search candidates",
                    False,
                    "tool_cache",
                ) from None

        try:
            batch = SearchBatch.model_validate(
                await self._adapter.search_batch(query, categories=categories, invoke=attempt)
            )
        except ExceptionGroup as exc:
            # Concurrent ledger/cache failures can occur before sibling
            # cancellation reaches them. Keep typed authority failures visible
            # to the Driver instead of turning exhaustion into an opaque crash.
            pending, errors = list(exc.exceptions), []
            while pending:
                error = pending.pop(0)
                if isinstance(error, ExceptionGroup):
                    pending.extend(error.exceptions)
                else:
                    errors.append(error)
            if errors and all(isinstance(error, (AppError, AdapterError)) for error in errors):
                # Integrity/lease errors must not be hidden by an accompanying
                # budget error and misinterpreted as permission to contract.
                selected = next(
                    (error for error in errors if error.code != "budget_exhausted"), errors[0]
                )
                raise selected from exc
            raise
        expected = {
            name for name, provider in self._providers.items() if provider.category in categories
        }
        if {outcome.source for outcome in batch.outcomes} != expected:
            raise AppError("invalid_state", "Search outcomes differ from authorized providers")
        return batch.model_dump(mode="json")
