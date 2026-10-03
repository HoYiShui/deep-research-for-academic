"""Unit tests for scout.research."""

import pytest

from domain.research.agents import scout
from infrastructure.fake import FakeLLM, FakeSearch


class _FakeRetrieval:
    async def retrieve(self, query, kb_id, top_k):
        return []


@pytest.mark.asyncio
async def test_scout_dedups_by_source_location_quote() -> None:
    search = FakeSearch()
    result = await scout.research({"sub_questions": ["q1", "q2"]}, search, _FakeRetrieval(), FakeLLM())
    # FakeSearch returns the same candidate for both queries; dedup keeps one.
    assert len(result["evidence"]) == 1


@pytest.mark.asyncio
async def test_scout_evidence_is_id_keyed_with_source_id() -> None:
    search = FakeSearch()
    result = await scout.research({"objective": "o"}, search, _FakeRetrieval(), FakeLLM())
    evidence = next(iter(result["evidence"].values()))
    assert evidence["source_id"] == "fake-1"
    assert evidence["evidence_id"].startswith("ev-")


@pytest.mark.asyncio
async def test_scout_registers_sources() -> None:
    search = FakeSearch()
    result = await scout.research({"objective": "o"}, search, _FakeRetrieval(), FakeLLM())
    source = result["sources"]["fake-1"]
    assert source["title"] == "Fake paper"
    assert source["source_tier"] == "peer_reviewed"  # paper source
    assert source["provenance"] == "arxiv"


@pytest.mark.asyncio
async def test_scout_extracts_claims_via_llm() -> None:
    search = FakeSearch()
    llm = FakeLLM(
        response='{"claims": [{"text": "method A outperforms baseline", '
        '"conditions": {}, "evidence_ids": []}]}'
    )
    result = await scout.research({"objective": "o"}, search, _FakeRetrieval(), llm)
    claim = next(iter(result["claims"].values()))
    assert claim["text"] == "method A outperforms baseline"
    assert claim["claim_id"].startswith("cl-")
    assert result["claim_evidence_links"] == []
