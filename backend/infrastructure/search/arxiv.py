"""arXiv Atom candidates: one request, no hidden retries or invented peer review."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from domain.ports import SearchResult
from infrastructure.search.http import RequestSpacing, SearchHTTP, error, query_text

_ATOM = "{http://www.w3.org/2005/Atom}"
_ID = r"(?:[0-9]{2}(?:0[1-9]|1[0-2])\.[0-9]{4,5}|[a-z][a-z.-]*/[0-9]{7})(?:v[1-9][0-9]*)?"
_EXPLICIT_ID = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?:arxiv(?:\s*:\s*|\s+)|https?://arxiv\.org/(?:abs|pdf)/)"
    rf"({_ID})(?![A-Za-z0-9./])",
    re.IGNORECASE,
)


def _query_params(query):
    identifiers = list(dict.fromkeys(match.group(1) for match in _EXPLICIT_ID.finditer(query)))
    if not identifiers and re.fullmatch(_ID, query, re.IGNORECASE):
        identifiers = [query]
    if len(identifiers) > 10:
        raise error("arxiv", "search_query_invalid")
    # Exact identifiers are metadata lookup, not terms in a prose all: query.
    return (
        {"id_list": ",".join(identifiers), "max_results": 10}
        if identifiers
        else {"search_query": f"all:{query}", "max_results": 10}
    )


class ArxivSearch:
    def __init__(
        self,
        timeout_s: float = 20,
        *,
        client: httpx.AsyncClient | None = None,
        spacing: RequestSpacing | None = None,
    ):
        self._http = SearchHTTP("arxiv", timeout_s, client)
        self._spacing = spacing or RequestSpacing(3)

    async def search(self, query: str) -> list[SearchResult]:
        query = query_text(query)
        params = _query_params(query)
        await self._spacing.wait()
        body = await self._http.request(
            "GET",
            "https://export.arxiv.org/api/query",
            params=params,
        )
        return _parse(body)

    async def aclose(self):
        await self._http.aclose()


def _parse(xml: str | bytes) -> list[SearchResult]:
    try:
        lowered = (xml if isinstance(xml, str) else xml.decode("utf-8-sig")).lower()
        if "<!doctype" in lowered or "<!entity" in lowered:
            raise ValueError("XML declarations are forbidden")
        root = ET.fromstring(xml)
        if root.tag != f"{_ATOM}feed":
            raise ValueError("Expected Atom feed")
        entries = root.findall(f"{_ATOM}entry")
        if len(entries) > 10:
            raise ValueError("Too many candidates")
        results = []
        for entry in entries:

            def text(name, entry=entry):
                return (entry.findtext(f"{_ATOM}{name}") or "").strip()

            link = text("id")
            parsed = urlsplit(link)
            if parsed.hostname != "arxiv.org" or not parsed.path.startswith("/abs/"):
                # arXiv can return a 200 Atom error feed, not an empty search.
                raise ValueError("Invalid arXiv candidate or error feed")
            version = re.search(r"v\d+$", parsed.path)
            results.append(
                SearchResult(
                    source_id=link,
                    source_type="paper",
                    title=" ".join(text("title").split()),
                    snippet=text("summary"),
                    url=link,
                    authors_or_publisher=[
                        (author.findtext(f"{_ATOM}name") or "").strip()
                        for author in entry.findall(f"{_ATOM}author")
                    ],
                    published_at=text("published"),
                    version=version.group() if version else "",
                    provider="arxiv",
                    source_tier="primary",
                    fulltext_url=f"https://arxiv.org/pdf/{parsed.path.removeprefix('/abs/')}",
                )
            )
        return results
    except (ET.ParseError, ValueError, ValidationError) as exc:
        raise error("arxiv", "search_response_invalid") from exc
