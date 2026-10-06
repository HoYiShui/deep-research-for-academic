"""Contract tests for ContentStorePort / FetchPort / Parser."""

from unittest.mock import AsyncMock

import pytest

from domain.ports import AdapterError
from infrastructure.fetch.http import DownloadedBody
from infrastructure.parser.pdf import HttpFetch, MinerUParser, MinioContentStore


@pytest.mark.asyncio
async def test_http_fetch_returns_text() -> None:
    downloader = AsyncMock()
    downloader.download.return_value = DownloadedBody(
        body=b"hello",
        final_url="https://example.com",
        media_type="text/plain",
        sha256="a" * 64,
        etag=None,
        last_modified=None,
        redirects=(),
    )
    fetch = HttpFetch(downloader)
    assert await fetch.fetch("web", "https://example.com") == "hello"
    downloader.download.assert_awaited_once_with("https://example.com")


@pytest.mark.asyncio
async def test_legacy_fetch_does_not_decode_pdf_as_text() -> None:
    downloader = AsyncMock()
    fetch = HttpFetch(downloader)
    with pytest.raises(AdapterError, match="fetch_parser_required"):
        await fetch.fetch("paper", "https://arxiv.org/pdf/1706.03762")
    downloader.download.assert_not_awaited()


@pytest.mark.asyncio
async def test_minio_content_store_and_parser_have_interface() -> None:
    with pytest.raises(AdapterError) as failure:
        await MinioContentStore().get("c1")
    assert failure.value.code == "content_store_not_configured"
    with pytest.raises(AdapterError) as parser_failure:
        await MinerUParser().parse("x.pdf")
    assert parser_failure.value.code == "parser_not_configured"
