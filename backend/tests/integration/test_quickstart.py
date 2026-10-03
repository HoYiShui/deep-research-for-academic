"""End-to-end quickstart loop (T042): auth -> clarify -> pipeline -> report.

Mirrors quickstart.md's minimal closed loop through the assembled Container
with fakes for all external dependencies. The report is structural (unified
skeleton shape), not yet the gold case-1 content — see docs/cases/case-1-*.
"""

import pytest

from application.auth_service import AuthService
from application.bootstrap import Container
from infrastructure.fake import FakeExecution, FakeRetrieval, FakeSearch, FakeStateStore
from infrastructure.storage.memory import (
    InMemoryCancel,
    InMemoryDocumentStore,
    InMemoryUserStore,
)


class _ScriptedLLM:
    """Returns clarify-shaped JSON for clarify prompts, plan-shaped for plan."""

    async def complete(self, prompt: str) -> str:
        if "section_plans" in prompt:
            return (
                '{"section_plans": [{"section_id": "s1", "objective": "o", '
                '"sub_questions": ["q1"]}]}'
            )
        return (
            '{"missing_fields": [], "questions": [], '
            '"brief_patch": {"task_type": "idea_exploration", "decision_goal": "goal"}, '
            '"assumptions": []}'
        )


def _drain(bus, session_id: str) -> list:
    events = []
    queue = bus.queue(session_id)
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_quickstart_minimal_loop() -> None:
    container = Container(
        llm=_ScriptedLLM(),
        search=FakeSearch(),
        retrieval=FakeRetrieval(),
        execution=FakeExecution(),
        store=FakeStateStore(),
        cancel=InMemoryCancel(),
        documents=InMemoryDocumentStore(),
        users=InMemoryUserStore(),
        auth=AuthService(InMemoryUserStore(), secret="a" * 32),
    )

    # 1. auth
    await container.auth.register("researcher@lab.org", "pw")
    token = await container.auth.login("researcher@lab.org", "pw")
    assert token["access_token"]

    # 2. create session
    start = await container.research.start()
    assert start["status"] == "clarify"
    session_id = start["session_id"]

    # 3. clarify (one round -> ready)
    result = await container.sessions.clarify_round(session_id, "design a phishing detector")
    assert result["status"] == "ready"

    # 4. pipeline
    task = container.research.spawn_pipeline(session_id, result["brief"])
    await task

    events = _drain(container.bus, session_id)
    phases = [e.phase for e in events if type(e).__name__ == "PhaseEvent"]
    assert phases == ["plan", "research", "analyze", "write", "review"]
    assert any(type(e).__name__ == "DoneEvent" for e in events)

    # 5. report
    report = await container.research.get_report(session_id)
    assert report is not None
    assert report["sections"]["s1"]["section_id"] == "s1"
    status = await container.research.get_status(session_id)
    assert status["status"] == "done"
