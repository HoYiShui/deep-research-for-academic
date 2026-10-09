"""OpenAlex works search: open-access papers with a direct PDF original.

Relevance search across journals, conferences and preprints. Only works with
an open-access PDF are returned, because research cites originals, never
abstracts. A query too specific for any match is relaxed by dropping trailing
terms (at most three requests); each request is still a single read.
"""

from __future__ import annotations

import json
import re

import httpx
from pydantic import ValidationError

from domain.ports import SearchResult
from infrastructure.search.http import RequestSpacing, SearchHTTP, error, query_text

_FIELDS = (
    "id,doi,title,publication_date,type,authorships,"
    "primary_location,best_oa_location,abstract_inverted_index"
)
_TERM = re.compile(r'"[^"]+"|[A-Za-z0-9][A-Za-z0-9.\-+]*')


def _terms(query):
    # OpenAlex ranks English metadata; CJK planning prose is not a paper query.
    return [term for term in _TERM.findall(query) if len(term.strip('"')) > 1]


def _relaxations(terms):
    """Full query first, then shorter prefixes; planner terms lead with the topic."""
    sizes = [len(terms)]
    for size in (max(3, round(len(terms) * 0.6)), 3):
        if size < sizes[-1]:
            sizes.append(size)
    return [" ".join(terms[:size]) for size in sizes]


def _abstract(index):
    if not isinstance(index, dict):
        return ""
    positions = [(at, word) for word, places in index.items() for at in places]
    return " ".join(word for _, word in sorted(positions))[:4000]


class OpenAlexSearch:
    def __init__(
        self,
        timeout_s: float = 20,
        *,
        mailto: str = "",
        client: httpx.AsyncClient | None = None,
        spacing: RequestSpacing | None = None,
    ):
        self._http = SearchHTTP("openalex", timeout_s, client)
        self._spacing = spacing or RequestSpacing(0.2)
        self._mailto = mailto

    async def search(self, query: str) -> list[SearchResult]:
        terms = _terms(query_text(query))
        if not terms:
            return []
        for text in _relaxations(terms):
            params = {
                "search": text,
                "filter": "is_oa:true",
                "per-page": 10,
                "select": _FIELDS,
            }
            if self._mailto:
                params["mailto"] = self._mailto
            await self._spacing.wait()
            body = await self._http.request("GET", "https://api.openalex.org/works", params=params)
            results = _parse(body)
            if results:
                return results
        return []

    async def aclose(self):
        await self._http.aclose()


def _parse(body: bytes) -> list[SearchResult]:
    try:
        data = json.loads(body)
        works = data["results"]
        if not isinstance(works, list):
            raise TypeError("Invalid OpenAlex results")
        results = []
        for work in works:
            pdf = (work.get("best_oa_location") or {}).get("pdf_url")
            title = " ".join((work.get("title") or "").split())
            if not pdf or not title or not pdf.startswith(("http://", "https://")):
                continue
            venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
            authors = [
                name
                for item in work.get("authorships") or []
                if (name := ((item.get("author") or {}).get("display_name") or "").strip())
            ][:20]
            try:
                result = SearchResult(
                    source_id=work["id"],
                    source_type="paper",
                    title=title[:4096],
                    snippet=_abstract(work.get("abstract_inverted_index")),
                    url=work.get("doi") or work["id"],
                    authors_or_publisher=[*authors, *([venue] if venue else [])],
                    published_at=work.get("publication_date") or "",
                    version=work.get("type") or "",
                    provider="openalex",
                    # A paper is a primary record of its own claims; OpenAlex
                    # metadata does not prove peer review, so no stronger tier.
                    source_tier="primary",
                    fulltext_url=pdf,
                )
            except ValidationError:
                continue  # One malformed record (e.g. a bad PDF URL) is not a failed search.
            results.append(result)
        return results
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise error("openalex", "search_response_invalid") from exc
