"""Unit tests for the clarify round cap (T054)."""

import pytest

from application.session_service import MAX_CLARIFY_ROUNDS, SessionService
from infrastructure.fake import FakeLLM, FakeStateStore

_CRITICAL_LLM = FakeLLM(
    response='{"missing_fields": ["decision_goal"], "questions": ["what is the goal?"], '
    '"brief_patch": {}, "assumptions": []}'
)


@pytest.mark.asyncio
async def test_clarify_asks_when_critical_missing() -> None:
    svc = SessionService(_CRITICAL_LLM, FakeStateStore())
    await svc.create("s1")
    result = await svc.clarify_round("s1", "design a detector")
    assert result["status"] == "ask"


@pytest.mark.asyncio
async def test_clarify_forces_ready_at_round_cap() -> None:
    store = FakeStateStore()
    svc = SessionService(_CRITICAL_LLM, store)
    await svc.create("s1")
    for _ in range(MAX_CLARIFY_ROUNDS):
        await store.append_message("s1", "user", "answer")
    result = await svc.clarify_round("s1", "final answer")
    # Past the cap the brief is frozen with conservative defaults (FR-003).
    assert result["status"] == "ready"
