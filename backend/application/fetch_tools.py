"""Download only authorized search candidates; durable results contain references."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from application.errors import AppError
from application.ports import DocumentFetchPort
from application.tool_budget import ToolBudgetRequest
from application.tool_calls import ToolCallService, ToolOutput
from domain.documents import FetchedDocument, ParsedDocument, ParserConfig
from domain.ports import AdapterError
from domain.research.agents.originals import validate_original
from domain.research.ids import canonical_hash
from domain.research.search import SearchResult
from domain.research.tool_calls import ToolCallIdentity


@dataclass(frozen=True)
class FetchBinding:
    create: Callable[[UUID, ParserConfig], DocumentFetchPort]
    provider: str = "http"
    revision: str = "dr4a-fetch-v1"


class FetchTools:
    def __init__(self, service: ToolCallService, binding: FetchBinding, knowledge):
        if (
            not callable(binding.create)
            or not binding.provider.strip()
            or not binding.revision.strip()
        ):
            raise ValueError("Fetch binding must specify a factory and versioned provider")
        self._service, self._binding, self._knowledge = service, binding, list(knowledge)
        self._adapter = None

    async def invoke(self, arguments, *, phase, unit, candidates, allow_uncertain_replay):
        run = self._service.claimed.run
        policy = run.config_snapshot.source_policy
        if (
            phase != "research"
            or run.phase != phase
            or unit is None
            or unit.phase != phase
            or unit.parameters.get("kind") != "query"
            or type(arguments) is not dict
            or set(arguments) != {"candidate_key"}
            or type(arguments["candidate_key"]) is not str
        ):
            raise AppError("invalid_state", "Fetch request differs from query unit authority")
        if policy.private_only:
            raise AppError("privacy_policy_conflict", "Private scope forbids external Fetch")
        raw = candidates.get(arguments["candidate_key"])
        if raw is None:
            raise AppError("invalid_state", "Fetch candidate was not returned by this query")
        candidate = SearchResult.model_validate_json(raw.model_dump_json())
        category = "papers" if candidate.source_type == "paper" else "web"
        if category not in policy.categories:
            raise AppError("invalid_state", "Fetch candidate exceeds frozen source scope")
        target = candidate.fulltext_url if candidate.source_type == "paper" else candidate.url
        if target is None:
            raise AdapterError(
                "fetch",
                "fetch_target_missing",
                "Original document target is missing",
                False,
                "fetch",
            )
        config = ParserConfig(parser_version=run.config_snapshot.versions.parser_version)
        if self._adapter is None:
            self._adapter = self._binding.create(run.run_id, config)
        adapter = self._adapter
        identity = ToolCallIdentity(
            run_id=run.run_id,
            tool="fetch",
            provider=self._binding.provider,
            version=canonical_hash(
                {"fetch": self._binding.revision, "parser": config.model_dump(mode="json")}
            ),
            # Download/parser output depends on the original target, not the
            # search snippet, claim state, chapter, or query. Authority was
            # checked above, so another authorized query may reuse the result.
            arguments={"source_type": candidate.source_type, "url": target},
            input_hash=canonical_hash({"source_type": candidate.source_type, "url": target}),
            source_policy=policy,
            knowledge_snapshot=self._knowledge,
        )

        def checked(value):
            fetched = FetchedDocument.model_validate(value)
            prefix = f"research-content/{run.run_id}/"
            if (
                fetched.parser_version != config.parser_version
                or fetched.content_ref.key != prefix + fetched.content_ref.sha256
                or fetched.parsed_content_ref.key != prefix + fetched.parsed_content_ref.sha256
                or (candidate.source_type == "paper" and fetched.media_type != "application/pdf")
            ):
                raise ValueError("Fetch references differ from frozen Run/parser/original scope")
            return fetched

        async def request():
            try:
                fetched = checked(await adapter.fetch(candidate))
            except (ValueError, TypeError):
                raise AdapterError(
                    "fetch", "fetch_response_invalid", "Invalid fetched document", False, "fetch"
                ) from None
            # Do not duplicate parsed text into the 10MiB tool cache. Its own
            # immutable content references are verified on every return/replay.
            return ToolOutput(content=fetched.model_dump(mode="json"), tokens_used=0)

        result = await self._service.invoke(
            identity,
            ToolBudgetRequest(tool="fetch", token_reservation=0, terminal=False),
            request,
            allow_uncertain_replay=allow_uncertain_replay,
        )
        try:
            fetched = checked(result.content)
            remaining = run.config_snapshot.limits.deadline_s - self._service.elapsed_s()
            if remaining <= 0:
                raise AppError("budget_exhausted", "Execution deadline is exhausted")
            async with asyncio.timeout(min(run.config_snapshot.timeouts_s.fetch, remaining)):
                parsed = ParsedDocument.model_validate(await adapter.read_parsed(fetched))
            validate_original(fetched, parsed)
        except TimeoutError:
            raise AdapterError(
                "fetch",
                "content_read_timeout",
                "Original content read exceeded its deadline",
                True,
                "tool_cache",
            ) from None
        except (ValueError, TypeError):
            raise AdapterError(
                "fetch",
                "content_invalid",
                "Stored original/parser handoff is invalid",
                False,
                "tool_cache",
            ) from None
        return {
            "fetched": fetched.model_dump(mode="json"),
            "parsed": parsed.model_dump(mode="json"),
        }
