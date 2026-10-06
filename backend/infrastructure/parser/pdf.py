"""Legacy content/parser interfaces, fail-closed until their migration tasks.

The text-only Fetch bridge delegates to the restricted downloader; it does
not implement the formal versioned FetchedDocument/Parser/content contract.
"""

from __future__ import annotations

from domain.ports import AdapterError
from infrastructure.fetch.http import RestrictedDownloader


class MinerUParser:
    """Parse a PDF into structured text (body + tables + formulas)."""

    async def parse(self, path: str) -> dict:
        """Parse a PDF; returns {"text": "...", "tables": [...], "formulas": [...]}."""
        # Actual versioned local parser is implemented in T043; until then a
        # missing parser is a dependency failure, never successful empty text.
        raise AdapterError(
            "parser",
            "parser_not_configured",
            "Local document parser is not configured",
            False,
            "parse",
        )


class MinioContentStore:
    """Unmigrated legacy chunk interface; never pretend missing content is valid.

    Tool result persistence uses infrastructure.storage.content_cache instead.
    Document/chunk lifecycle storage is implemented in the ingestion tasks.
    """

    def __init__(self) -> None:
        self._bucket = None

    async def get(self, chunk_id: str) -> str:
        """Read a chunk's text from MinIO."""
        raise AdapterError(
            "minio",
            "content_store_not_configured",
            "Document content storage is not configured",
            False,
            "get",
        )


class HttpFetch:
    """Legacy text-only bridge; network safety uses the same restricted downloader.

    Formal FetchedDocument/Parser/content references are implemented separately;
    this compatibility interface must never decode PDF bytes as fake text.
    """

    def __init__(self, downloader: RestrictedDownloader | None = None):
        self._downloader = downloader if downloader is not None else RestrictedDownloader()

    async def fetch(self, source_type: str, doc_ref: str) -> str:
        if source_type != "web":
            raise AdapterError(
                "fetch", "fetch_parser_required", "Document parser is required", False, "fetch"
            )
        result = await self._downloader.download(doc_ref)
        if result.media_type not in {"text/html", "text/plain"}:
            raise AdapterError(
                "fetch", "fetch_parser_required", "Document parser is required", False, "fetch"
            )
        try:
            return result.body.decode("utf-8")
        except UnicodeError as exc:
            raise AdapterError(
                "fetch", "fetch_response_invalid", "Text encoding is unsupported", False, "fetch"
            ) from exc
