"""Controlled public socket replay -> actual parser -> real immutable MinIO.

Not a live external-page/real-PDF test. Original bytes come from the HTTP stack,
never search snippets; production parser/storage are used without fallback.
"""

import asyncio
from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from application.settings import Settings
from domain.documents import ParserConfig
from domain.ports import AdapterError, SearchResult
from infrastructure.fetch.document import HTTPDocumentFetch
from infrastructure.parser.html import HTML_PARSER_VERSION, HTMLDocumentParser
from infrastructure.storage.content import MinioContentStore
from tests.contract.test_document_parser import HTML
from tests.contract.test_fetch import downloader, response


@pytest.mark.asyncio
async def test_original_parser_locations_reopen_and_no_network_on_read(object_cache):
    settings = Settings.load()
    store = MinioContentStore(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        object_cache.bucket,
        secure=settings.minio_secure,
    )
    parser = HTMLDocumentParser(store)
    run = uuid4()
    candidate = SearchResult(
        source_id="fixture-web",
        source_type="web",
        title="Controlled original",
        snippet="THIS ABSTRACT IS NOT ORIGINAL EVIDENCE",
        url="https://example.com/article",
    )
    transport, net, _ = downloader([response(HTML.encode(), media="text/html")])
    fetch = HTTPDocumentFetch(
        store, parser, ParserConfig(parser_version=HTML_PARSER_VERSION), run, downloader=transport
    )
    try:
        result = await fetch.fetch(candidate)
        assert result.hash == sha256(HTML.encode()).hexdigest()
        assert result.content_ref.key == f"research-content/{run}/{result.hash}"
        assert result.content_ref.size == len(HTML.encode())
        assert len(net.connected) == 1
        found = await fetch.read_parsed(result)
        assert found.blocks[1].content == "Use the same dataset & split."
        assert "ABSTRACT" not in found.model_dump_json()
        assert [block.location for block in found.blocks] == result.locations
        assert len(net.connected) == 1  # reading does not repay/reparse/redownload
        reopened_parser = AsyncMock()
        no_network = AsyncMock()
        reopened = HTTPDocumentFetch(
            store,
            reopened_parser,
            ParserConfig(parser_version=HTML_PARSER_VERSION),
            run,
            downloader=no_network,
        )
        assert await reopened.read_parsed(result) == found
        reopened_parser.parse.assert_not_awaited()
        no_network.download.assert_not_awaited()
        other = HTTPDocumentFetch(
            store, parser, ParserConfig(parser_version=HTML_PARSER_VERSION), uuid4()
        )
        with pytest.raises(AdapterError, match="content_scope_mismatch"):
            await other.read_parsed(result)
        await store.delete(result.content_ref.key)
        with pytest.raises(AdapterError, match="content_missing"):
            await reopened.read_parsed(result)
        assert len(net.connected) == 1
        objects = await asyncio.to_thread(
            lambda: list(store._client.list_objects(store.bucket, recursive=True))
        )
        assert len(objects) == 1  # parsed object survives original loss, but is unusable
    finally:
        await parser.close()
        await store.close()


@pytest.mark.asyncio
async def test_paper_fetch_never_uses_abstract_as_body_and_parser_failure_does_not_publish(
    object_cache,
):
    settings = Settings.load()
    store = MinioContentStore(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        object_cache.bucket,
        secure=settings.minio_secure,
    )
    parser = HTMLDocumentParser(store)
    transport, net, _ = downloader(
        [response(b"%PDF-1.7\ncontrolled PDF bytes", media="application/pdf")]
    )
    candidate = SearchResult(
        source_id="fixture-paper",
        source_type="paper",
        title="Paper",
        snippet="fake abstract",
        url="https://example.com/abstract",
        fulltext_url="https://example.com/paper.pdf",
    )
    fetch = HTTPDocumentFetch(
        store,
        parser,
        ParserConfig(parser_version=HTML_PARSER_VERSION),
        uuid4(),
        downloader=transport,
    )
    try:
        with pytest.raises(AdapterError, match="parser_not_configured"):
            await fetch.fetch(candidate)
        assert b"GET /paper.pdf " in b"".join(net.writes)
        assert b"GET /abstract " not in b"".join(net.writes)
        objects = await asyncio.to_thread(
            lambda: list(store._client.list_objects(store.bucket, recursive=True))
        )
        assert len(objects) == 1  # raw orphan only, no invented parsed mapping
        assert (await store.head(objects[0].object_name)).media_type == "application/pdf"
    finally:
        await parser.close()
        await store.close()
