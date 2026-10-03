"""Integration test for the real-agent wiring (T050).

Runs the full pipeline (plan -> research -> analyze -> write -> review) with a
scripted LLM and verifies the id-keyed entities flow through every phase.
"""

import pytest

from application.orchestrator import Orchestrator
from application.sse import EventBus
from infrastructure.fake import FakeExecution, FakeRetrieval, FakeSearch, FakeStateStore
from infrastructure.storage.memory import InMemoryCancel


class _ScriptedLLM:
    """Returns phase-appropriate JSON for each agent prompt."""

    async def complete(self, prompt: str) -> str:
        if "section_plans" in prompt:
            return '{"section_plans": [{"section_id": "s1", "objective": "o", "sub_questions": ["q1"]}]}'
        if '"claims"' in prompt:
            return '{"claims": [{"text": "method A works", "conditions": {}, "evidence_ids": []}]}'
        if '"observations"' in prompt:
            return '{"observations": []}'
        if '"metrics"' in prompt:
            return (
                '{"metrics": [{"evaluated_method": "A", '
                '"evaluation_context": {"dataset_and_version": "r4.2", "metric": "ACC", '
                '"split_or_protocol": "temporal"}}]}'
            )
        if '"content"' in prompt:
            return (
                '{"content": "Section prose.", "bindings": [{"statement_id": "st-0", '
                '"claim_ids": [], "cited_evidence_ids": [], "artifact_ids": []}]}'
            )
        if '"issues"' in prompt:
            return '{"issues": []}'
        return "{}"


def _drain(bus: EventBus, session_id: str) -> list:
    events = []
    queue = bus.queue(session_id)
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_pipeline_produces_all_id_keyed_entities() -> None:
    bus = EventBus()
    store = FakeStateStore()
    orchestrator = Orchestrator(
        bus, InMemoryCancel(), store, _ScriptedLLM(), FakeSearch(), FakeRetrieval(), FakeExecution()
    )
    await orchestrator.run("agents-s1", {"task_type": "idea_exploration"})

    events = _drain(bus, "agents-s1")
    phases = [e.phase for e in events if type(e).__name__ == "PhaseEvent"]
    assert phases == ["plan", "research", "analyze", "write", "review"]
    assert any(type(e).__name__ == "DoneEvent" for e in events)

    snapshot = await store.load_latest_snapshot("agents-s1", "done")
    assert snapshot is not None
    # Id-keyed entities flowed through every phase.
    assert snapshot["sources"]  # registered from FakeSearch
    assert snapshot["evidence"]  # gathered from FakeSearch
    assert snapshot["claims"]  # extracted via LLM
    assert snapshot["draft_sections"]  # written via LLM
    assert snapshot["draft_claim_bindings"] is not None
    assert snapshot["final_report"]["sections"] == snapshot["draft_sections"]
