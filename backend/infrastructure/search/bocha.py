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

    async def search(self, query: str) -> list[SearchResult]:
        """Search the web and return candidates."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.bochaai.com/v1/web-search",
                json={"query": query, "freshness": "noLimit"},
                headers={"Authorization": f"Bearer {os.environ.get('BOCHA_API_KEY', '')}"},
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
