"""Independent phase handoff across actual MinIO scopes, controlled HTTP bytes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from application.settings import Settings
from cli.research_tools import ResearchDebugTools
from domain.documents import FetchedDocument, ParsedDocument, ParserConfig
from domain.ports import AdapterError
from domain.research.agents.originals import evidence_from_original, register_original
from domain.research.ids import canonical_hash
from domain.research.search import SearchResult
from domain.research.state import PipelineState
from infrastructure.fetch.document import HTTPDocumentFetch
from infrastructure.parser.html import HTML_PARSER_VERSION, HTMLDocumentParser
from scripts.verify_research_phase import audit
from tests.contract.test_document_parser import HTML
from tests.contract.test_fetch import downloader, response
from tests.integration.test_mono_document_content import content_store as shared_content_store
from tests.unit.test_phase_contracts import plans
from tests.unit.test_state import initial_state

content_store = shared_content_store


async def test_authorized_debug_fetch_retains_verified_old_raw_reference_only(
    content_store, monkeypatch
):
    config = ParserConfig(parser_version=HTML_PARSER_VERSION)
    parser = HTMLDocumentParser(content_store)
    candidate = SearchResult(
        source_id="provider-id",
        source_type="web",
        title="Controlled original",
        snippet="Not evidence",
        url="https://example.org/doc",
        source_tier="official",
    )
    old_scope, debug_scope = uuid4(), uuid4()
    transport, _, _ = downloader([response(HTML.encode(), media="text/html")])
    old_fetch = HTTPDocumentFetch(content_store, parser, config, old_scope, downloader=transport)
    old = await old_fetch.fetch(candidate)
    old_parsed = await old_fetch.read_parsed(old)
    source = register_original(candidate, old, old_parsed, retrieved_at=datetime.now(UTC))
    transport, network, _ = downloader([response(HTML.encode(), media="text/html")])
    debug_fetch = HTTPDocumentFetch(
        content_store, parser, config, debug_scope, downloader=transport
    )
    usage = {"fetch_calls": 0}
    tools = ResearchDebugTools(
        None,
        initial_state().run_metadata.config,
        usage,
        fake=True,
        sources={source.source_id: source},
    )
    tools.store, tools.fetch = content_store, debug_fetch
    tools.for_unit(SimpleNamespace(parameters={"kind": "query", "query": "controlled"}))
    key = canonical_hash(candidate)
    tools.candidates[key] = candidate
    before = source.model_dump_json()
    try:
        body = await tools.invoke("fetch", {"candidate_key": key})
        bound = FetchedDocument.model_validate(body["fetched"])
        assert bound.content_ref == old.content_ref
        assert bound.parsed_content_ref.key.startswith(f"research-content/{debug_scope}/")
        parsed = ParsedDocument.model_validate(body["parsed"])
        assert (
            register_original(candidate, bound, parsed, retrieved_at=datetime.now(UTC)).source_id
            == source.source_id
        )
        assert source.model_dump_json() == before
        assert usage["fetch_calls"] == 1 and len(network.connected) == 1
        assert await tools.invoke("fetch", {"candidate_key": key}) == body
        assert usage["fetch_calls"] == 1 and len(network.connected) == 1
        # The shared Fetch reader has not been weakened to accept mixed scopes.
        with pytest.raises(AdapterError, match="content_scope_mismatch"):
            await debug_fetch.read_parsed(bound)
        evidence = evidence_from_original(
            source,
            bound,
            parsed,
            block_index=1,
            quote=parsed.blocks[1].content,
            evidence_type="protocol",
        )
        data = initial_state().model_dump()
        data["run_metadata"]["config"]["versions"]["parser_version"] = HTML_PARSER_VERSION
        state = PipelineState.model_validate(
            data
            | {
                "phase": "research",
                "section_plans": plans(),
                "sources": {source.source_id: source},
                "evidence": {evidence.evidence_id: evidence},
            }
        )
        settings = Settings.load().model_copy(update={"minio_bucket": content_store.bucket})
        monkeypatch.setattr("scripts.verify_research_phase.Settings.load", lambda: settings)
        with pytest.raises(ValueError, match="scope"):
            await audit(state, debug_scope)
        verified = await audit(state, debug_scope, input_sources={source.source_id: source})
        assert verified[0]["original_quote_verified"]
        foreign = source.model_copy(
            update={"content_object_key": f"research-content/{uuid4()}/{source.content_hash}"}
        )
        with pytest.raises(ValueError, match="scope"):
            await audit(state, debug_scope, input_sources={source.source_id: foreign})
        await content_store.delete(old.content_ref.key)
        with pytest.raises(AdapterError, match="content_missing"):
            await tools.invoke("fetch", {"candidate_key": key})
        assert usage["fetch_calls"] == 1 and len(network.connected) == 1
    finally:
        await parser.close()
