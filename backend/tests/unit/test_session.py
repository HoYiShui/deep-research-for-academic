"""Pure mono clarification candidates, without storage or task spawning."""

import asyncio
import json
from uuid import uuid4

import pytest

from application.errors import AppError
from application.records import SessionInput
from application.session_service import SessionService
from domain.ports import AdapterError
from domain.research.models import Message, PartialResearchBrief, SessionState
from infrastructure.fake_research import FakeClock
from tests.integration.test_mono_transactions import candidate
from tests.unit.test_mono_clarify import assessment, core


class Model:
    def __init__(self, response=None, fail=False):
        self.response = assessment() if response is None else response
        self.fail = fail
        self.prompts = []

    async def complete(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise OSError("secret-url")
        return json.dumps(self.response)


def initial(draft=None):
    base = candidate(uuid4()).session
    return SessionInput(
        session=SessionState.model_validate(
            base.model_dump()
            | {
                "status": "ask",
                "brief_draft": draft or {},
                "pending_questions": [],
                "missing_fields": [],
            }
        ),
        history=(),
    )


def subsequent(value, change):
    return SessionInput(session=change.session, history=(*value.history, *change.messages))


async def test_initial_returns_ask_or_confirm_without_freezing_or_mutation():
    clock, model = FakeClock(), Model()
    service = SessionService(model, clock)
    value = initial()
    old = value.model_dump()
    ask = await service.assess_initial(value)
    assert ask.session.status == "ask" and ask.session.run_id is None
    assert ask.session.clarification_round == 0
    assert ask.session.brief_version == ask.session.revision == 1
    assert [message.sequence for message in ask.messages] == [1, 2]
    assert ask.messages[0].kind == "initial"
    assert ask.brief.frozen_at is None and ask.brief.confirmed_by is None
    assert value.model_dump() == old
    model.response = assessment(brief_patch=core())
    confirm = await service.assess_initial(value)
    assert confirm.session.status == "confirm"
    assert confirm.session.run_id is None and confirm.brief.frozen_at is None


async def test_round_cap_keeps_ask_and_stops_automatic_model_calls():
    model, service = Model(), None
    service = SessionService(model, FakeClock())
    value = initial()
    value = subsequent(value, await service.assess_initial(value))
    for _ in range(3):
        value = subsequent(value, await service.assess_round(value, "unclear answer"))
    assert value.session.status == "ask"
    assert value.session.clarification_limit_reached
    assert value.session.clarification_round == 3
    assert len(model.prompts) == 4
    old = value.model_dump()
    result = await service.assess_round(value, "more unstructured information")
    assert result.session.status == "ask"
    assert result.session.brief_version == result.session.revision == 5
    assert result.session.clarification_round == 4
    assert len(model.prompts) == 4
    assert value.model_dump() == old
    patched = await service.assess_round(
        value, "Explicit brief fields", brief_patch=PartialResearchBrief(**core())
    )
    assert patched.session.status == "confirm"
    assert patched.brief.frozen_at is None
    assert len(model.prompts) == 4


async def test_model_failure_does_not_change_old_state_or_add_messages():
    service = SessionService(Model(fail=True), FakeClock())
    value = initial()
    old = value.model_dump()
    with pytest.raises(AdapterError, match="dependency_unavailable"):
        await service.assess_initial(value)
    assert value.model_dump() == old
    value = SessionInput(
        session=SessionState.model_validate(
            value.session.model_dump()
            | {"pending_questions": ["What is the goal?"], "missing_fields": ["decision_goal"]}
        ),
        history=(),
    )
    old = value.model_dump()
    with pytest.raises(AdapterError):
        await service.assess_round(value, "answer")
    assert value.model_dump() == old


async def test_rejection_is_always_ask_and_does_not_call_model_after_limit():
    model = Model(assessment(brief_patch=core()))
    service = SessionService(model, FakeClock())
    value = initial()
    value = subsequent(value, await service.assess_initial(value))
    rejected = await service.assess_rejection(value, "Change the evaluation scope")
    assert rejected.session.status == "ask"
    assert rejected.session.brief_version == 2
    assert rejected.session.clarification_round == 1
    assert rejected.messages[0].kind == "rejection"
    limited = SessionInput(
        session=SessionState.model_validate(
            value.session.model_dump()
            | {"clarification_round": 3, "clarification_limit_reached": True}
        ),
        history=value.history,
    )
    before = len(model.prompts)
    rejected = await service.assess_rejection(limited, "Change the scope")
    assert rejected.session.status == "ask"
    assert rejected.session.brief_draft == limited.session.brief_draft
    assert len(model.prompts) == before


async def test_confirmation_validates_without_model_freeze_or_spawn():
    model = Model(assessment(brief_patch=core()))
    service = SessionService(model, FakeClock())
    confirm = await service.assess_initial(initial())
    before = confirm.model_dump()
    frozen = await service.validate_confirmation(confirm.session, 1)
    assert frozen.confirmed_by == confirm.session.owner_id
    assert frozen.expected_revision == 1
    assert confirm.model_dump() == before
    assert len(model.prompts) == 1
    with pytest.raises(AppError, match="stale_brief"):
        await service.validate_confirmation(confirm.session, 2)
    with pytest.raises(AppError, match="invalid_session_state"):
        await service.assess_round(
            SessionInput(session=confirm.session, history=tuple(confirm.messages)),
            "edit via wrong endpoint",
        )


async def test_history_sent_to_model_is_bounded():
    model, clock = Model(), FakeClock()
    value = initial()
    history = tuple(
        Message(
            message_id=uuid4(),
            session_id=value.session.session_id,
            sequence=i + 1,
            role="user",
            kind="answer",
            content=("old-history-marker" if i == 0 else "x" * 16000),
            assessment=None,
            brief_version=1,
            created_at=clock.now_utc(),
        )
        for i in range(20)
    )
    value = SessionInput(session=value.session, history=history)
    result = await SessionService(model, clock).assess_round(value, "answer")
    assert [message.sequence for message in result.messages] == [21, 22]
    context = json.loads(
        model.prompts[-1].split("<research_context>\n", 1)[1].split("\n</research_context>", 1)[0]
    )
    assert len(context["history"]) == 8
    assert all(len(item["content"]) == 8000 for item in context["history"])
    assert "old-history-marker" not in model.prompts[-1]


async def test_valid_strings_that_overflow_combined_assumptions_fail_closed():
    model = Model(assessment(brief_patch=core(), assumptions=["x" * 8000]))
    service = SessionService(model, FakeClock())
    value = initial()
    old = value.model_dump()
    with pytest.raises(AppError, match="model_output_invalid"):
        await service.assess_initial(value)
    assert value.model_dump() == old


async def test_unpatched_semantic_gap_remains_after_automatic_limit():
    model = Model(
        assessment(
            brief_patch=core() | {"scope": "A dataset"},
            field_reasons={"scope": "Dataset identity must be explicit"},
        )
    )
    service = SessionService(model, FakeClock())
    value = initial()
    value = subsequent(value, await service.assess_initial(value))
    value = SessionInput(
        session=SessionState.model_validate(
            value.session.model_dump()
            | {"clarification_round": 3, "clarification_limit_reached": True}
        ),
        history=value.history,
    )
    unchanged = await service.assess_round(
        value,
        "Change goal",
        brief_patch=PartialResearchBrief(decision_goal="Compare detection mechanisms"),
    )
    assert unchanged.session.status == "ask" and "scope" in unchanged.session.missing_fields
    explicit = await service.assess_round(
        value,
        "Specify dataset",
        brief_patch=PartialResearchBrief(scope="Public dataset version A, fixed split B"),
    )
    assert explicit.session.status == "confirm"
    assert len(model.prompts) == 1


async def test_rejection_model_failure_keeps_old_confirm():
    model = Model(assessment(brief_patch=core()))
    service = SessionService(model, FakeClock())
    value = initial()
    value = subsequent(value, await service.assess_initial(value))
    old = value.model_dump()
    model.fail = True
    with pytest.raises(AdapterError):
        await service.assess_rejection(value, "Change scope")
    assert value.model_dump() == old


async def test_model_timeout_leaves_old_state_unchanged():
    class WaitingModel:
        async def complete(self, prompt):
            await asyncio.Future()

    value = initial()
    old = value.model_dump()
    with pytest.raises(AdapterError, match="dependency_unavailable"):
        await SessionService(WaitingModel(), FakeClock(), timeout_s=0.01).assess_initial(value)
    assert value.model_dump() == old


async def test_model_cancellation_propagates_without_candidate():
    class CancelledModel:
        async def complete(self, prompt):
            raise asyncio.CancelledError

    value = initial()
    old = value.model_dump()
    with pytest.raises(asyncio.CancelledError):
        await SessionService(CancelledModel(), FakeClock()).assess_initial(value)
    assert value.model_dump() == old
