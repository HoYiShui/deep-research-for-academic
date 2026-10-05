"""Bocha web search adapter.

Response shape: ``{code, data: {webPages: {value: [{name, url, snippet, ...}]}}}``
(verified against the live API).
"""

from __future__ import annotations

import os

import httpx

from domain.ports import SearchResult


class BochaSearch:
    """SearchPort implementation via the Bocha web search API."""

    def __init__(self, api_key: str | None = None, timeout_s: float = 20) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def search(self, query: str) -> list[SearchResult]:
        """Search the web and return candidates."""
        key = self._api_key if self._api_key is not None else os.environ.get("BOCHA_API_KEY", "")
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(
                "https://api.bochaai.com/v1/web-search",
                json={"query": query, "freshness": "noLimit"},
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        web_pages = data.get("data", {}).get("webPages", {})
        return [
            SearchResult(
                source_id=item.get("url", ""),
                source_type="web",
                title=item.get("name", ""),
                snippet=item.get("snippet", ""),
                url=item.get("url", ""),
            )
            for item in web_pages.get("value", [])
        ]
