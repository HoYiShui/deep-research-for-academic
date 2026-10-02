"""Contract tests: verify fake adapters satisfy the port contracts."""

import pytest

from application.ports import StateStorePort
from domain.ports import LLMPort, SearchPort, SearchResult
from infrastructure.fake import FakeLLM, FakeSearch, FakeStateStore


@pytest.mark.asyncio
async def test_fake_llm_satisfies_llm_port() -> None:
    llm: LLMPort = FakeLLM(response="ok")
    assert await llm.complete("hi") == "ok"


@pytest.mark.asyncio
async def test_fake_search_satisfies_search_port() -> None:
    search: SearchPort = FakeSearch()
    results = await search.search("query")
    assert results and isinstance(results[0], SearchResult)


@pytest.mark.asyncio
async def test_fake_state_store_satisfies_state_store_port() -> None:
    store: StateStorePort = FakeStateStore()
    await store.save_session("s1", {"phase": "plan"})
    assert await store.load_session("s1") == {"phase": "plan"}


@pytest.mark.asyncio
async def test_fake_state_store_latest_snapshot() -> None:
    store: StateStorePort = FakeStateStore()
    await store.save_snapshot("s1", "research", {"v": 1})
    await store.save_snapshot("s1", "research", {"v": 2})
    assert await store.load_latest_snapshot("s1", "research") == {"v": 2}
