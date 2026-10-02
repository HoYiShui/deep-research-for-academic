"""Integration tests for the persistence slice (T037).

Verifies: two-state freeze handover, same-phase-latest snapshots across rework,
recovery-by-reconstruction, and terminate-on-persistence-failure.
"""

from unittest.mock import patch

import pytest

from application.bootstrap import Container
from application.orchestrator import Orchestrator
from application.sse import EventBus
from domain.research.state import PipelineState
from infrastructure.fake import FakeExecution, FakeLLM, FakeRetrieval, FakeSearch, FakeStateStore
from infrastructure.storage.memory import InMemoryCancel


def _drain(bus: EventBus, session_id: str) -> list:
    events = []
    queue = bus.queue(session_id)
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_freeze_handover_passes_frozen_brief_to_pipeline() -> None:
    store = FakeStateStore()
    llm = FakeLLM(
        response='{"missing_fields": [], "questions": [], '
        '"brief_patch": {"task_type": "idea_exploration"}, "assumptions": []}'
    )
    container = Container(
        llm=llm,
        search=FakeSearch(),
        retrieval=FakeRetrieval(),
        execution=FakeExecution(),
        store=store,
    )
    start = await container.research.start()
    session_id = start["session_id"]

    result = await container.sessions.clarify_round(session_id, "design a detector")
    assert result["status"] == "ready"

    task = container.research.spawn_pipeline(session_id, result["brief"])
    await task

    snapshot = await store.load_latest_snapshot(session_id, "plan")
    assert snapshot["brief"] == result["brief"]
    assert snapshot["brief"]["task_type"] == "idea_exploration"


@pytest.mark.asyncio
async def test_rework_keeps_latest_snapshot_per_phase() -> None:
    store = FakeStateStore()
    llm = FakeLLM(
        response='{"section_plans": [{"section_id": "s1", "objective": "o", '
        '"sub_questions": ["q1"]}]}'
    )
    orchestrator = Orchestrator(
        EventBus(), InMemoryCancel(), store, llm, FakeSearch(), FakeRetrieval(), FakeExecution()
    )
    with patch(
        "domain.research.agents.critic.review",
        side_effect=[
            [{"issue_id": "i1", "issue_type": "missing_source", "severity": "critical",
              "fillable": True}],
            [],
        ],
    ):
        await orchestrator.run("persist-s1", {"task_type": "idea_exploration"})

    research_snapshots = [
        s for sid, _, s in store._snapshots if sid == "persist-s1" and s.get("phase") == "research"
    ]
    assert len(research_snapshots) == 2  # rework re-enters research
    latest = await store.load_latest_snapshot("persist-s1", "research")
    assert latest == research_snapshots[-1]


@pytest.mark.asyncio
async def test_recovery_reconstructs_state_from_snapshot() -> None:
    store = FakeStateStore()
    llm = FakeLLM(
        response='{"section_plans": [{"section_id": "s1", "objective": "o", '
        '"sub_questions": ["q1"]}]}'
    )
    orchestrator = Orchestrator(
        EventBus(), InMemoryCancel(), store, llm, FakeSearch(), FakeRetrieval(), FakeExecution()
    )
    await orchestrator.run("persist-s2", {"task_type": "idea_exploration"})

    snapshot = await store.load_latest_snapshot("persist-s2", "review")
    state = PipelineState(**snapshot)
    assert state.phase == "review"
    assert state.draft_sections  # write-phase output is the review-phase input


class _FailingStore:
    """State store whose snapshots always fail (PG down)."""

    async def save_session(self, session_id: str, state: dict) -> None:
        pass

    async def load_session(self, session_id: str) -> dict | None:
        return None

    async def save_snapshot(self, session_id: str, phase: str, state: dict) -> None:
        raise RuntimeError("pg down")

    async def load_latest_snapshot(self, session_id: str, phase: str) -> dict | None:
        return None


@pytest.mark.asyncio
async def test_pg_unavailable_terminates_with_error() -> None:
    bus = EventBus()
    orchestrator = Orchestrator(
        bus, InMemoryCancel(), _FailingStore(), FakeLLM(), FakeSearch(), FakeRetrieval(), FakeExecution()
    )
    await orchestrator.run("persist-s3", {"task_type": "idea_exploration"})

    events = _drain(bus, "persist-s3")
    errors = [e for e in events if type(e).__name__ == "ErrorEvent"]
    done = [e for e in events if type(e).__name__ == "DoneEvent"]
    assert any(e.code == "persistence_unavailable" for e in errors)
    assert not done
