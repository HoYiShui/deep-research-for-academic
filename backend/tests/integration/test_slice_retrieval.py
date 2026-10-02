"""Integration tests for the real-search slice (T036).

Verifies per-source failure semantics: timeout -> retry once -> coverage gap;
unavailable -> skip the source (degrade) and keep going; and that the
orchestrator drains gaps and emits a non-fatal "source_unavailable" error.
"""


import pytest

from application.orchestrator import Orchestrator
from application.sse import EventBus
from domain.ports import SearchResult
from infrastructure.fake import FakeExecution, FakeLLM, FakeRetrieval, FakeStateStore
from infrastructure.search.composite import CompositeSearch
from infrastructure.storage.memory import InMemoryCancel


class _GoodSource:
    def __init__(self, count: int = 0) -> None:
        self.calls = 0
        self._count = count

    async def search(self, query: str) -> list[SearchResult]:
        self.calls += 1
        return [
            SearchResult(source_id="good-1", source_type="paper", title="t", snippet="s")
        ]


class _TimeoutSource:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str) -> list[SearchResult]:
        self.calls += 1
        raise TimeoutError()


class _UnavailableSource:
    async def search(self, query: str) -> list[SearchResult]:
        raise RuntimeError("connection refused")


@pytest.mark.asyncio
async def test_composite_skips_unavailable_and_records_gaps() -> None:
    good = _GoodSource()
    composite = CompositeSearch(
        [("good", good), ("dead", _UnavailableSource()), ("slow", _TimeoutSource())]
    )
    results = await composite.search("query")
    assert [r.source_id for r in results] == ["good-1"]
    reasons = {g["source"]: g["reason"] for g in composite.take_gaps()}
    assert reasons == {"dead": "unavailable", "slow": "timeout"}


@pytest.mark.asyncio
async def test_timeout_source_is_retried_once() -> None:
    slow = _TimeoutSource()
    composite = CompositeSearch([("slow", slow)])
    await composite.search("query")
    assert slow.calls == 2  # one attempt + one retry
    assert composite.take_gaps() == [{"source": "slow", "reason": "timeout"}]


@pytest.mark.asyncio
async def test_orchestrator_degrades_and_still_completes() -> None:
    bus = EventBus()
    store = FakeStateStore()
    llm = FakeLLM(
        response='{"section_plans": [{"section_id": "s1", "objective": "o", '
        '"sub_questions": ["q1"]}]}'
    )
    search = CompositeSearch([("arxiv", _GoodSource()), ("bocha", _UnavailableSource())])
    orchestrator = Orchestrator(
        bus, InMemoryCancel(), store, llm, search, FakeRetrieval(), FakeExecution()
    )
    await orchestrator.run("search-s1", {"task_type": "idea_exploration"})

    events = _drain(bus, "search-s1")
    errors = [e for e in events if type(e).__name__ == "ErrorEvent"]
    done = [e for e in events if type(e).__name__ == "DoneEvent"]
    # Non-fatal: degraded source emits an error but the pipeline still completes.
    assert any(e.code == "source_unavailable" for e in errors)
    assert any(e.status == "completed" for e in done)


def _drain(bus: EventBus, session_id: str) -> list:
    events = []
    queue = bus.queue(session_id)
    while not queue.empty():
        events.append(queue.get_nowait())
    return events
