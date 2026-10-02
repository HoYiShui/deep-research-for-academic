"""PDF parser (MinerU) and content/fetch adapters.

ContentStorePort reads locally stored chunk text (MinIO); FetchPort pulls
external full text (arXiv/web). MinerU parsing is lazy-loaded.
"""

from __future__ import annotations

import httpx


class MinerUParser:
    """Parse a PDF into structured text (body + tables + formulas)."""

    async def parse(self, path: str) -> dict:
        """Parse a PDF; returns {"text": "...", "tables": [...], "formulas": [...]}."""
        # MinerU integration is wired in the S4 slice; placeholder now.
        return {"text": "", "tables": [], "formulas": []}


class MinioContentStore:
    """ContentStorePort implementation backed by MinIO."""

    def __init__(self) -> None:
        self._bucket = None

    async def get(self, chunk_id: str) -> str:
        """Read a chunk's text from MinIO."""
        return ""  # wired in S4; placeholder satisfies the contract


class HttpFetch:
    """FetchPort implementation: pull external full text via HTTP."""

    async def fetch(self, source_type: str, doc_ref: str) -> str:
        """Fetch a document by URL; returns its text."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(doc_ref)
            resp.raise_for_status()
        return resp.text
