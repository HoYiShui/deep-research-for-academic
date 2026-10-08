"""Isolated public research debug I/O, no PG/Run authority or fake originals."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from application.errors import AppError
from cli import output
from domain.documents import FetchedDocument, ParserConfig
from domain.ports import AdapterError
from domain.research.agents.originals import register_original
from domain.research.ids import canonical_hash
from domain.research.search import SearchBatch, SearchOutcome
from infrastructure.fetch.document import HTTPDocumentFetch
from infrastructure.parser.html import HTML_PARSER_VERSION, HTMLDocumentParser
from infrastructure.parser.mineru_output import MINERU_PARSER_VERSION
from infrastructure.parser.pdf import MinerUDocumentParser
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.composite import CompositeSearch
from infrastructure.storage.content import MinioContentStore, content_key


class ResearchDebugTools:
    def __init__(self, settings, config, usage, *, fake, sources=None):
        self.config, self.usage, self.fake = config, usage, fake
        self.sources = dict(sources or {})
        self.scope, self.search, self.store, self.parser, self.fetch = None, None, None, None, None
        self.unit, self.candidates, self.results = None, {}, {}
        if fake:
            return
        if config.versions.parser_version not in {HTML_PARSER_VERSION, MINERU_PARSER_VERSION}:
            raise output.EnvError("Research debug parser version is not configured")
        self.scope = uuid4()  # Never write content under the input snapshot's Run ID.
        self.usage["artifact_scope"] = str(self.scope)
        timeout = max(0.1, config.timeouts_s.search - 1)
        self.search = CompositeSearch(
            [
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
        self.parser = (
            HTMLDocumentParser(self.store)
            if config.versions.parser_version == HTML_PARSER_VERSION
            else MinerUDocumentParser(
                self.store, settings.mineru_models_dir, timeout_s=config.timeouts_s.parser
            )
        )
        self.fetch = HTTPDocumentFetch(
            self.store,
            self.parser,
            ParserConfig(parser_version=config.versions.parser_version),
            self.scope,
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
            self.usage.setdefault("search_outcomes", []).append(
                {
                    "unit_id": self.unit.unit_id,
                    "outcomes": [
                        {
                            "source": item.source,
                            "status": item.status,
                            "attempts": item.attempts,
                            "failure": (
                                item.failure.model_dump(mode="json") if item.failure else None
                            ),
                        }
                        for item in batch.outcomes
                    ],
                }
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
        fetched = await self._retain_original_reference(candidate, fetched, parsed)
        return {
            "fetched": fetched.model_dump(mode="json"),
            "parsed": parsed.model_dump(mode="json"),
        }

    async def _retain_original_reference(self, candidate, fetched, parsed):
        """Read-only bridge for input facts, after fresh Fetch integrity checks.

        This is not a Run cache hit: download/parser costs still belong to this
        invocation. Never pass mixed-scope references back to HTTPDocumentFetch.
        Its fresh-scope checks stay intact; only the verified worker handoff uses
        the original immutable reference from the supplied public snapshot.
        """
        source = register_original(candidate, fetched, parsed, retrieved_at=datetime.now(UTC))
        existing = self.sources.get(source.source_id)
        if existing is None or existing.content_hash != fetched.hash:
            return fetched
        old_key = existing.content_object_key
        if existing.data_classification != "public" or not old_key:
            raise AppError("privacy_policy_conflict", "Debug original is not a public content fact")
        content_key(old_key)
        if not old_key.startswith("research-content/") or old_key.rsplit("/", 1)[1] != fetched.hash:
            raise AppError("invalid_state", "Debug original reference differs from its byte hash")
        digest, size = hashlib.sha256(), 0
        async for chunk in await self.store.get(old_key):
            size += len(chunk)
            if size > fetched.content_ref.size:
                break
            digest.update(chunk)
        if size != fetched.content_ref.size or digest.hexdigest() != fetched.hash:
            raise AdapterError(
                "fetch",
                "content_hash_mismatch",
                "Snapshot original failed integrity validation",
                False,
                "fetch",
            )
        data = fetched.model_dump()
        data["content_ref"]["key"] = old_key
        return FetchedDocument.model_validate(data)

    async def close(self):
        if self.search is not None:
            await self.search.aclose()
        if self.parser is not None:
            await self.parser.close()
        if self.store is not None:
            await self.store.close()
