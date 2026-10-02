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
    await store.create_session("s1")
    assert await store.get_session_status("s1") == "clarify"
    await store.set_session_status("s1", "ready")
    assert await store.get_session_status("s1") == "ready"
    await store.append_message("s1", "user", "hello")
    assert await store.list_messages("s1") == [{"role": "user", "content": "hello"}]
    await store.save_brief("s1", {"task_type": "idea_exploration"})
    assert await store.load_brief("s1") == {"task_type": "idea_exploration"}
    await store.save_report("s1", {"sections": []})
    assert await store.load_report("s1") == {"sections": []}


@pytest.mark.asyncio
async def test_fake_state_store_latest_snapshot() -> None:
    store: StateStorePort = FakeStateStore()
    await store.save_snapshot("s1", "research", {"v": 1})
    await store.save_snapshot("s1", "research", {"v": 2})
    assert await store.load_latest_snapshot("s1", "research") == {"v": 2}
