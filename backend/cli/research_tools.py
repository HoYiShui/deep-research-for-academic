"""Isolated public research debug I/O, no PG/Run authority or fake originals."""

from uuid import uuid4

from application.errors import AppError
from cli import output
from domain.documents import ParserConfig
from domain.research.ids import canonical_hash
from domain.research.search import SearchBatch, SearchOutcome
from infrastructure.fetch.document import HTTPDocumentFetch
from infrastructure.parser.html import HTML_PARSER_VERSION, HTMLDocumentParser
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.composite import CompositeSearch
from infrastructure.storage.content import MinioContentStore


class ResearchDebugTools:
    def __init__(self, settings, config, usage, *, fake):
        self.config, self.usage, self.fake = config, usage, fake
        self.scope, self.search, self.store, self.parser, self.fetch = None, None, None, None, None
        self.unit, self.candidates, self.results = None, {}, {}
        if fake:
            return
        if config.versions.parser_version != HTML_PARSER_VERSION:
            raise output.EnvError(
                "Research debug currently requires parser_version=dr4a-html-v1; PDF Parser is not configured"
            )
        self.scope = uuid4()  # Never write content under the input snapshot's Run ID.
        self.usage["artifact_scope"] = str(self.scope)
        timeout = max(0.1, config.timeouts_s.search - 1)
        self.search = CompositeSearch(
            [
                ("arxiv", ArxivSearch(timeout_s=timeout)),
                (
                    "bocha",
                    BochaSearch(
                        api_key=settings.bocha_api_key.get_secret_value(), timeout_s=timeout
                    ),
                ),
            ],
            timeout_s=timeout,
        )
        self.store = MinioContentStore(
            settings.minio_endpoint,
            settings.minio_access_key.get_secret_value(),
            settings.minio_secret_key.get_secret_value(),
            settings.minio_bucket,
            secure=settings.minio_secure,
        )
        self.parser = HTMLDocumentParser(self.store)
        self.fetch = HTTPDocumentFetch(
            self.store, self.parser, ParserConfig(parser_version=HTML_PARSER_VERSION), self.scope
        )

    def for_unit(self, unit):
        self.unit, self.candidates = unit, {}

    async def invoke(self, tool, payload):
        if self.unit is None or self.unit.parameters.get("kind") != "query":
            raise AppError("invalid_state", "Research debug tool requires a query unit")
        if tool == "search":
            if (
                type(payload) is not dict
                or set(payload) != {"query"}
                or payload["query"] != self.unit.parameters["query"]
            ):
                raise AppError("invalid_state", "Debug search differs from query authority")
            categories = frozenset(self.config.source_policy.categories) & {"papers", "web"}
            if self.config.source_policy.private_only or not categories:
                raise AppError("privacy_policy_conflict", "Debug scope forbids external search")
            if self.fake:
                return SearchBatch(
                    outcomes=[
                        SearchOutcome(source=category, attempts=1, status="empty")
                        for category in sorted(categories)
                    ]
                ).model_dump(mode="json")

            async def attempt(_name, _query, _ordinal, operation):
                if self.usage["search_calls"] >= self.config.limits.search_calls:
                    raise AppError("budget_exhausted", "Debug search budget is exhausted")
                self.usage["search_calls"] += 1
                return await operation()

            batch = await self.search.search_batch(
                payload["query"], categories=categories, invoke=attempt
            )
            self.candidates.update({canonical_hash(item): item for item in batch.items})
            return batch.model_dump(mode="json")
        if tool != "fetch" or type(payload) is not dict or set(payload) != {"candidate_key"}:
            raise AppError("invalid_state", "Invalid research debug tool request")
        if not isinstance(payload["candidate_key"], str):
            raise AppError("invalid_state", "Debug Fetch candidate key must be a string")
        candidate = self.candidates.get(payload["candidate_key"])
        if candidate is None:
            raise AppError(
                "invalid_state", "Debug Fetch candidate was not returned by current query"
            )
        target = candidate.fulltext_url if candidate.source_type == "paper" else candidate.url
        key = (candidate.source_type, target)
        if key not in self.results:
            if self.usage["fetch_calls"] >= self.config.limits.fetch_calls:
                raise AppError("budget_exhausted", "Debug Fetch budget is exhausted")
            self.usage["fetch_calls"] += 1
            self.results[key] = await self.fetch.fetch(candidate)
        fetched = self.results[key]
        parsed = await self.fetch.read_parsed(fetched)
        return {
            "fetched": fetched.model_dump(mode="json"),
            "parsed": parsed.model_dump(mode="json"),
        }

    async def close(self):
        if self.search is not None:
            await self.search.aclose()
        if self.parser is not None:
            await self.parser.close()
        if self.store is not None:
            await self.store.close()
