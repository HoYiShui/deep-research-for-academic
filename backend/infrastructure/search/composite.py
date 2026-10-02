"""Multi-source search with per-source failure semantics.

plan.md failure table for Search:
- timeout -> retry once -> give up, mark a coverage gap
- empty -> mark a coverage gap (not an error)
- unavailable -> degrade: skip the source, keep using the others

Each source is a named SearchPort; gaps are recorded on this composite and
drained by the orchestrator after the research phase.
"""

from __future__ import annotations

from typing import Any

import httpx

from domain.ports import SearchPort, SearchResult


class _SourceUnavailable(Exception):
    """A source is down and should be skipped (degraded), not retried."""


class CompositeSearch:
    """SearchPort that fans out across named sources and degrades per-source."""

    def __init__(self, sources: list[tuple[str, SearchPort]]) -> None:
        self._sources = sources
        self.gaps: list[dict[str, Any]] = []

    async def search(self, query: str) -> list[SearchResult]:
        """Search every source, skipping degraded ones and recording gaps."""
        results: list[SearchResult] = []
        for name, source in self._sources:
            try:
                found, gap = await self._search_with_retry(source, query)
            except _SourceUnavailable:
                self.gaps.append({"source": name, "reason": "unavailable"})
                continue
            if gap:
                self.gaps.append({"source": name, "reason": gap})
            elif not found:
                self.gaps.append({"source": name, "reason": "empty"})
            results.extend(found)
        return results

    def take_gaps(self) -> list[dict[str, Any]]:
        """Return and clear the accumulated coverage gaps."""
        gaps, self.gaps = self.gaps, []
        return gaps

    async def _search_with_retry(
        self, source: SearchPort, query: str
    ) -> tuple[list[SearchResult], str | None]:
        """Search one source, retrying once on timeout.

        Returns:
            (results, gap_reason_or_none) where gap_reason is "timeout" when
            both attempts time out.
        """
        try:
            return await source.search(query), None
        except (TimeoutError, httpx.TimeoutException):
            try:
                return await source.search(query), None
            except (TimeoutError, httpx.TimeoutException):
                return [], "timeout"
        except Exception as exc:
            raise _SourceUnavailable from exc
