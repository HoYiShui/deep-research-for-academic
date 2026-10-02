"""Integration tests for the real-LLM slice (T035).

Verifies: DeepSeekLLM retry/backoff, and the orchestrator's terminate-on-LLM-
failure semantics plus the happy-path phase sequence.
"""

from unittest.mock import AsyncMock, patch

import pytest

from application.orchestrator import Orchestrator
from application.sse import EventBus
from infrastructure.fake import FakeExecution, FakeLLM, FakeRetrieval, FakeSearch, FakeStateStore
from infrastructure.llm.deepseek import DeepSeekLLM
from infrastructure.storage.memory import InMemoryCancel


def _make_llm(retries: int = 2, backoff: float = 0.0) -> DeepSeekLLM:
    with patch("infrastructure.llm.deepseek.AsyncAnthropic"):
        return DeepSeekLLM(retries=retries, backoff=backoff)


def _drain(bus: EventBus, session_id: str) -> list:
    events = []
    queue = bus.queue(session_id)
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_deepseek_retries_transient_failure_then_succeeds() -> None:
    llm = _make_llm()
    llm._complete_once = AsyncMock(
        side_effect=[RuntimeError("boom"), RuntimeError("boom"), "ok"]
    )
    assert await llm.complete("hi") == "ok"
    assert llm._complete_once.await_count == 3


@pytest.mark.asyncio
async def test_deepseek_exhausts_retries_and_raises() -> None:
    llm = _make_llm()
    llm._complete_once = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await llm.complete("hi")
    assert llm._complete_once.await_count == 3


def _orchestrator(bus: EventBus, store, llm) -> Orchestrator:
    return Orchestrator(
        bus, InMemoryCancel(), store, llm, FakeSearch(), FakeRetrieval(), FakeExecution()
    )


@pytest.mark.asyncio
async def test_pipeline_emits_phases_in_order_and_done() -> None:
    bus = EventBus()
    store = FakeStateStore()
    llm = FakeLLM(
        response='{"section_plans": [{"section_id": "s1", "objective": "o", '
        '"sub_questions": ["q1"]}]}'
    )
    await _orchestrator(bus, store, llm).run("llm-s1", {"task_type": "idea_exploration"})

    events = _drain(bus, "llm-s1")
    phases = [e.phase for e in events if type(e).__name__ == "PhaseEvent"]
    assert phases == ["plan", "research", "analyze", "write", "review"]
    assert any(type(e).__name__ == "DoneEvent" for e in events)


class _FailingLLM:
    async def complete(self, prompt: str) -> str:
        raise RuntimeError("llm down")


@pytest.mark.asyncio
async def test_llm_failure_terminates_with_error_event() -> None:
    bus = EventBus()
    store = FakeStateStore()
    await _orchestrator(bus, store, _FailingLLM()).run("llm-s2", {"task_type": "idea_exploration"})

    events = _drain(bus, "llm-s2")
    errors = [e for e in events if type(e).__name__ == "ErrorEvent"]
    done = [e for e in events if type(e).__name__ == "DoneEvent"]
    assert errors and errors[0].code == "step_failed"
    assert not any(e.status == "completed" for e in done)
    # The failure is persisted for recovery.
    snapshots = [s for _, _, s in store._snapshots if s.get("errors")]
    assert snapshots
