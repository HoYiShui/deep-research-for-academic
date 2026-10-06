"""Bocha web candidates; valid emptiness differs from HTTP/provider errors."""

from __future__ import annotations

import json
import os

import httpx
from pydantic import ValidationError

from domain.ports import SearchResult
from infrastructure.search.http import SearchHTTP, error, query_text


class BochaSearch:
    """SearchPort implementation via the Bocha web search API."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_s: float = 20,
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key
        self._http = SearchHTTP("bocha", timeout_s, client)

    async def search(self, query: str) -> list[SearchResult]:
        query = query_text(query)
        key = self._api_key if self._api_key is not None else os.environ.get("BOCHA_API_KEY", "")
        if not key.strip():
            raise error("bocha", "search_not_configured")
        body = await self._http.request(
            "POST",
            "https://api.bochaai.com/v1/web-search",
            json={"query": query, "freshness": "noLimit", "count": 10, "summary": False},
            headers={"Authorization": f"Bearer {key}"},
        )
        return _parse(body)

    async def aclose(self):
        await self._http.aclose()


def _parse(body: bytes) -> list[SearchResult]:
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            raise ValueError("Missing provider status")
        code = payload["code"]
        if code != 200:
            raise error("bocha", "search_provider_error", code == 429 or code >= 500)
        items = payload["data"]["webPages"]["value"]
        if not isinstance(items, list) or len(items) > 50:
            raise ValueError("Invalid candidates")
        results = []
        for item in items:
            publisher = item.get("siteName")
            results.append(
                SearchResult(
                    source_id=item["url"],
                    source_type="web",
                    title=item["name"],
                    snippet=item.get("snippet", ""),
                    url=item["url"],
                    authors_or_publisher=[] if publisher in (None, "") else [publisher],
                    published_at="" if item.get("datePublished") is None else item["datePublished"],
                    provider="bocha",
                    source_tier="unknown",
                )
            )
        return results
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError) as exc:
        raise error("bocha", "search_response_invalid") from exc
