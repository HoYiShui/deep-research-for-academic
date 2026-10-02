"""arXiv search adapter (free public API, no key required)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from domain.ports import SearchResult

_NAMESPACE = {"a": "http://www.w3.org/2005/Atom"}


class ArxivSearch:
    """SearchPort implementation via the arXiv API."""

    async def search(self, query: str) -> list[SearchResult]:
        """Search arXiv and return candidates."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": f"all:{query}", "max_results": 10},
            )
            resp.raise_for_status()
        return _parse(resp.text)


def _parse(xml: str) -> list[SearchResult]:
    """Parse an arXiv Atom response into SearchResult list."""
    root = ET.fromstring(xml)
    results: list[SearchResult] = []
    for entry in root.findall("a:entry", _NAMESPACE):
        title = (entry.findtext("a:title", default="", namespaces=_NAMESPACE) or "").strip()
        summary = (entry.findtext("a:summary", default="", namespaces=_NAMESPACE) or "").strip()
        link = (entry.findtext("a:id", default="", namespaces=_NAMESPACE) or "").strip()
        results.append(
            SearchResult(
                source_id=link,
                source_type="paper",
                title=title,
                snippet=summary,
                url=link,
            )
        )
    return results
