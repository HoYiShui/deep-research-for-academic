"""Unit tests for scout.research."""

import pytest

from domain.research.agents import scout
from infrastructure.fake import FakeSearch


class _FakeRetrieval:
    async def retrieve(self, query, kb_id, top_k):
        return []


@pytest.mark.asyncio
async def test_scout_dedups_by_source_location_quote() -> None:
    search = FakeSearch()
    result = await scout.research({"sub_questions": ["q1", "q2"]}, search, _FakeRetrieval())
    # FakeSearch returns the same candidate for both queries; dedup keeps one.
    assert len(result["evidence"]) == 1


@pytest.mark.asyncio
async def test_scout_evidence_has_source_id() -> None:
    search = FakeSearch()
    result = await scout.research({"objective": "o"}, search, _FakeRetrieval())
    assert result["evidence"][0]["source_id"] == "fake-1"
