"""Unit tests for scout citation_trace and gap_fill (T055)."""

import pytest

from domain.research.agents import scout
from infrastructure.fake import FakeSearch


@pytest.mark.asyncio
async def test_gap_fill_searches_for_uncovered_claims() -> None:
    search = FakeSearch()
    claims = {"c1": {"claim_id": "c1", "text": "method A works"}}
    coverage = {"gaps": [{"claim_id": "c1", "reason": "no_evidence"}]}
    result = await scout.gap_fill({"objective": "o"}, claims, coverage, search)
    assert result["evidence"]
    assert result["sources"]


@pytest.mark.asyncio
async def test_gap_fill_no_gaps_returns_empty() -> None:
    result = await scout.gap_fill({"objective": "o"}, {}, {"gaps": []}, FakeSearch())
    assert result == {"evidence": {}, "sources": {}}


@pytest.mark.asyncio
async def test_citation_trace_traces_secondary_sources() -> None:
    sources = {"s1": {"source_id": "s1", "source_tier": "secondary", "title": "A blog"}}
    result = await scout.citation_trace(sources, FakeSearch())
    assert result["evidence"]
    assert result["sources"]


@pytest.mark.asyncio
async def test_citation_trace_ignores_primary_sources() -> None:
    sources = {"s1": {"source_id": "s1", "source_tier": "peer_reviewed", "title": "A paper"}}
    result = await scout.citation_trace(sources, FakeSearch())
    assert result == {"evidence": {}, "sources": {}}
