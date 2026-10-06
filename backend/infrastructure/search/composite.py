"""Bounded parallel search; explicit local outcomes, no shared pipeline gaps.

Providers do exactly one physical request. An optional attempt invoker lets the
application meter/cache EVERY source attempt before I/O. Ledger/lease/budget
errors from that invoker must propagate, never become source degradation.
The list-returning search/take_gaps pair is a legacy compatibility surface only.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import httpx

from domain.ports import AdapterError, SearchPort, SearchResult
from domain.research.search import SearchBatch, SearchFailure, SearchOutcome
from infrastructure.search.http import error, query_text

SearchOperation = Callable[[], Awaitable[list[SearchResult]]]
AttemptInvoker = Callable[[str, str, int, SearchOperation], Awaitable[list[SearchResult]]]


class CompositeSearch:
    def __init__(
        self,
        sources: list[tuple[str, SearchPort]],
        *,
        timeout_s: float = 20,
        semaphore: asyncio.Semaphore | None = None,
        source_categories: dict[str, Literal["papers", "web"]] | None = None,
    ):
        if not sources or len(sources) > 16 or len({name for name, _ in sources}) != len(sources):
            raise ValueError("Search requires 1-16 uniquely named sources")
        if any(not name.strip() or len(name) > 100 for name, _ in sources):
            raise ValueError("Invalid search source name")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Search timeout must be finite and positive")
        self._sources = list(sources)
        self._timeout_s = timeout_s
        self._semaphore = semaphore if semaphore is not None else asyncio.Semaphore(4)
        self._categories = dict(
            {"arxiv": "papers", "bocha": "web"} if source_categories is None else source_categories
        )
        if any(value not in {"papers", "web"} for value in self._categories.values()):
            raise ValueError("Invalid search source category")
        self.gaps: list[dict[str, Any]] = []

    async def search_batch(
        self,
        query: str,
        *,
        categories: frozenset[str] | None = None,
        invoke: AttemptInvoker | None = None,
        retry: bool = True,
    ) -> SearchBatch:
        query = query_text(query)
        if type(retry) is not bool:
            raise ValueError("Search retry authority must be an explicit boolean")
        if categories is not None and not categories <= {"papers", "web"}:
            raise ValueError("External search cannot access knowledge_base")
        sources = self._sources
        if categories is not None:
            if any(name not in self._categories for name, _ in sources):
                raise ValueError("Category-filtered search requires explicit source categories")
            sources = [
                (name, source) for name, source in sources if self._categories[name] in categories
            ]
        if not sources:
            raise AdapterError(
                "search", "no_search_sources", "No authorized search source", False, "search"
            )
        # TaskGroup cancels and joins siblings when invoker/ledger/control fails.
        # Ordinary provider errors are returned as outcomes inside each task.
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(self._search_source(name, source, query, invoke, retry))
                    for name, source in sources
                ]
        except ExceptionGroup as exc:
            # Preserve a single application failure for Runner's typed failure path.
            if len(exc.exceptions) == 1:
                raise exc.exceptions[0] from exc
            raise
        return SearchBatch(outcomes=[task.result() for task in tasks])

    async def search(self, query: str) -> list[SearchResult]:
        batch = await self.search_batch(query)
        self.gaps.extend(
            {
                "source": outcome.source,
                "reason": (
                    "empty"
                    if outcome.status == "empty"
                    else "timeout"
                    if outcome.failure.code == "search_timeout"
                    else "unavailable"
                ),
            }
            for outcome in batch.outcomes
            if outcome.status != "ok"
        )
        if batch.all_failed:
            raise AdapterError(
                "search", "all_search_sources_failed", "All search sources failed", False, "search"
            )
        return batch.items

    def take_gaps(self) -> list[dict[str, Any]]:
        """Return and clear the accumulated coverage gaps."""
        gaps, self.gaps = self.gaps, []
        return gaps

    async def _search_source(self, name, source, query, invoke, retry) -> SearchOutcome:
        async def operation():
            # Deadline applies to one provider operation, not ledger reservation/cache.
            async with self._semaphore:
                try:
                    async with asyncio.timeout(self._timeout_s):
                        found = await source.search(query)
                    if not isinstance(found, list) or len(found) > 50:
                        raise ValueError("Invalid search result list")
                    return [SearchResult.model_validate(item) for item in found]
                except AdapterError:
                    raise
                except (TimeoutError, httpx.TimeoutException) as exc:
                    raise error(name, "search_timeout", True) from exc
                except (ValueError, TypeError) as exc:
                    raise error(name, "search_response_invalid") from exc
                except Exception as exc:
                    raise error(name, "search_unavailable") from exc

        for attempt in range(1, 3 if retry else 2):
            try:
                found = (
                    await operation()
                    if invoke is None
                    else await invoke(name, query, attempt, operation)
                )
                return SearchOutcome(
                    source=name,
                    attempts=attempt,
                    status="ok" if found else "empty",
                    items=found,
                )
            except AdapterError as exc:
                if exc.operation != "search":
                    raise
                failure = SearchFailure(code=exc.code, retryable=exc.retryable)
                if not retry or attempt == 2 or not exc.retryable:
                    return SearchOutcome(
                        source=name, attempts=attempt, status="failed", failure=failure
                    )
                # Bounded backoff outside physical provider request; attempts remain visible.
                await asyncio.sleep(0.05)
        raise AssertionError("Unreachable search retry state")

    async def aclose(self):
        for _, source in self._sources:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()
