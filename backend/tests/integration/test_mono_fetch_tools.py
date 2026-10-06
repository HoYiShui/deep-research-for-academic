"""Run candidate gate + actual restricted HTTP/parser/MinIO + PG tool ledger."""

import asyncio
from datetime import UTC, datetime

import pytest

from application.errors import AppError
from application.fetch_tools import FetchBinding
from application.settings import Settings
from domain.documents import FetchedDocument, ParsedDocument
from domain.ports import AdapterError
from domain.research.agents.originals import evidence_from_original, register_original
from domain.research.ids import canonical_hash
from domain.research.search import SearchResult
from infrastructure.fetch.document import HTTPDocumentFetch
from infrastructure.parser.html import HTML_PARSER_VERSION, HTMLDocumentParser
from tests.contract.test_document_parser import HTML
from tests.contract.test_fetch import downloader, response
from tests.integration.test_mono_document_content import (
    content_store as shared_content_store,
)
from tests.integration.test_mono_run_driver import world
from tests.integration.test_mono_search_tools import Provider, binding

content_store = shared_content_store


@pytest.mark.parametrize("lose_original", [False, True])
async def test_fetch_requires_current_query_candidate_and_cache_never_downloads_again(
    pg_database, object_cache, content_store, monkeypatch, lose_original
):
    # Freeze the real parser version in this invocation's isolated Run config.
    monkeypatch.setattr(
        "tests.integration.test_mono_transactions.Settings",
        lambda: Settings(parser_version=HTML_PARSER_VERSION),
    )
    transport, network, _ = downloader([response(HTML.encode(), media="text/html")])
    parser = HTMLDocumentParser(content_store)
    factory_calls, queries, authorized_keys = [], [], []

    def create(run_id, config):
        factory_calls.append((run_id, config.parser_version))
        return HTTPDocumentFetch(content_store, parser, config, run_id, downloader=transport)

    async def before(value, context, *_):
        if value.phase != "research" or context.unit.parameters["kind"] != "query":
            return
        with pytest.raises(AppError, match="not returned"):
            await context.invoke("fetch", {"candidate_key": "f" * 64})
        if authorized_keys:
            with pytest.raises(AppError, match="not returned"):
                await context.invoke("fetch", {"candidate_key": authorized_keys[-1]})
        batch = await context.invoke("search", {"query": context.unit.parameters["query"]})
        candidate = batch["outcomes"][1]["items"][0]
        key = canonical_hash(candidate)
        authorized_keys.append(key)
        for invalid in (
            {"candidate_key": key, "url": "http://127.0.0.1/"},
            {"candidate_key": canonical_hash(candidate | {"url": "https://example.org/changed"})},
        ):
            with pytest.raises(AppError, match="invalid_state"):
                await context.invoke("fetch", invalid)
        result = await context.invoke("fetch", {"candidate_key": key})
        assert len(network.connected) == 1
        fetched, parsed = (
            FetchedDocument.model_validate(result["fetched"]),
            ParsedDocument.model_validate(result["parsed"]),
        )
        source = register_original(
            candidate=SearchResult.model_validate(candidate),
            fetched=fetched,
            parsed=parsed,
            retrieved_at=datetime.now(UTC),
        )
        evidence = evidence_from_original(
            source,
            fetched,
            parsed,
            block_index=1,
            quote=parsed.blocks[1].content,
            evidence_type="protocol",
        )
        assert evidence.location.line_start and "ABSTRACT" not in evidence.quote_or_raw_content
        assert await context.invoke("fetch", {"candidate_key": key}) == result
        queries.append(result)
        if lose_original:
            await content_store.delete(fetched.content_ref.key)
            with pytest.raises(AdapterError, match="content_missing"):
                await context.invoke("fetch", {"candidate_key": key})
            assert len(network.connected) == 1
            raise AdapterError(
                "fetch", "controlled_missing_original", "Stop after deletion test", False, "test"
            )

    pool, store, user, commit, claimed, _, _, _, _, _, make_driver = await world(
        pg_database, object_cache, before_worker=before
    )
    driver = make_driver(
        search=binding(Provider("paper", empty=True), Provider("web")), fetch=FetchBinding(create)
    )
    try:
        if lose_original:
            with pytest.raises(AdapterError, match="controlled_missing_original"):
                await driver.execute(claimed, asyncio.Event())
        else:
            await driver.execute(claimed, asyncio.Event())
            assert len(queries) > 1  # candidate authorization is rebuilt per unit.
        assert len(factory_calls) == len(network.connected) == 1
        assert (
            await pool.fetchval("SELECT count(*) FROM tool_call_attempts WHERE tool='fetch'") == 1
        )
        budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
        assert budget.used.fetch_calls == 1 and budget.pending.fetch_calls == 0
    finally:
        await parser.close()
