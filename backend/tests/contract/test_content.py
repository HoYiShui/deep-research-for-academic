"""Contract tests for ContentStorePort / FetchPort / Parser."""

from unittest.mock import AsyncMock, patch

import pytest

from domain.ports import AdapterError

from infrastructure.parser.pdf import HttpFetch, MinerUParser, MinioContentStore


@pytest.mark.asyncio
async def test_http_fetch_returns_text() -> None:
    with patch("infrastructure.parser.pdf.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=type("R", (), {"text": "hello", "raise_for_status": lambda s: None})()
        )
        fetch = HttpFetch()
        assert await fetch.fetch("web", "http://x") == "hello"


@pytest.mark.asyncio
async def test_minio_content_store_and_parser_have_interface() -> None:
    with pytest.raises(AdapterError) as failure:
        await MinioContentStore().get("c1")
    assert failure.value.code == "content_store_not_configured"
    parsed = await MinerUParser().parse("x.pdf")
    assert set(parsed) == {"text", "tables", "formulas"}
