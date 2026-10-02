"""Bocha web search adapter.

Note: the exact Bocha endpoint and response shape are verified in the S3
integration slice; this is the initial contract-following implementation.
"""

from __future__ import annotations

import os

import httpx

from domain.ports import SearchResult


class BochaSearch:
    """SearchPort implementation via the Bocha web search API."""

    async def search(self, query: str) -> list[SearchResult]:
        """Search the web and return candidates."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.bochaai.com/v1/web-search",
                json={"query": query, "freshness": "noLimit"},
                headers={"Authorization": f"Bearer {os.environ.get('BOCHA_API_KEY', '')}"},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            SearchResult(
                source_id=item.get("url", ""),
                source_type="web",
                title=item.get("name", ""),
                snippet=item.get("summary", ""),
                url=item.get("url", ""),
            )
            for item in data.get("webPages", {}).get("value", [])
        ]
