"""Mono clarification candidates and explicitly temporary pre-mono compatibility."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID, uuid4

from pydantic import ValidationError

from application.errors import AppError
from application.ports import StateStorePort
from application.records import SessionChange, SessionInput, ValidatedFrozenInput
from domain.ports import AdapterError, ClockPort, LLMPort
from domain.research.agents import architect
from domain.research.machine import assess_brief, legacy_decide_status
from domain.research.models import (
    BriefDecision,
    BriefRecord,
    ClarifyAssessment,
    Message,
    PartialResearchBrief,
    ResearchBrief,
    SessionState,
    SourceSelection,
)

# Automatic follow-up model limit; never a reason to freeze an incomplete brief.
MAX_CLARIFY_ROUNDS = 3


class LegacySessionService:
    """Pre-mono caller compatibility, removed during the T011/T012 cutover."""

    def __init__(self, llm: LLMPort, store: StateStorePort) -> None:
        self._llm = llm
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}

    async def create(self, session_id: str, query: str = "") -> dict:
        """Create a new session and seed the initial research request.

        Args:
            session_id: The session to create.
            query: The initial research request, seeded into the brief draft.
        """
        await self._store.create_session(session_id, "clarify")
        if query:
            await self._store.save_brief(session_id, {"query": query})
        return {"session_id": session_id, "status": "clarify"}

    async def clarify_round(self, session_id: str, answer: str) -> dict:
        """Advance one clarify round, serialized per session.

        Args:
            session_id: The session to advance.
            answer: The user's answer to the previous question (or the query).

        Returns:
            {"status": "ask" | "ready", "questions": [...]}.
        """
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            brief = await self._store.load_brief(session_id) or {}
            judgment = await architect.legacy_clarify(self._llm, brief, answer)
            brief.update(judgment.get("brief_patch", {}))

            messages = await self._store.list_messages(session_id)
            if len(messages) >= MAX_CLARIFY_ROUNDS:
                status = "ready"  # conservative default once the round cap is reached
            else:
                status = legacy_decide_status(judgment.get("missing_fields", []))
            await self._store.append_message(session_id, "user", answer)
            await self._store.save_brief(session_id, brief, brief.get("task_type", ""))
            await self._store.set_session_status(session_id, status)

            return {
                "status": status,
                "questions": judgment.get("questions", []),
                "brief": brief,
            }


class SessionService:
    """Mono-v1 candidate transformations, with no persistence or spawning."""

    def __init__(
        self,
        llm: LLMPort,
        clock: ClockPort,
        *,
        max_rounds: int = MAX_CLARIFY_ROUNDS,
        timeout_s: float = 60,
        id_factory: Callable[[], UUID] = uuid4,
    ):
        if type(max_rounds) is not int or not 0 <= max_rounds <= 3:
            raise ValueError("Clarification limit must be between zero and three")
        self.llm, self.clock = llm, clock
        self.max_rounds, self.timeout_s, self.id_factory = max_rounds, timeout_s, id_factory

    async def _model(self, value: SessionInput, answer: str, draft, selection):
        try:
            return await architect.clarify(
                self.llm,
                draft=draft,
                query=value.session.query,
                answer=answer,
                source_selection=selection,
                pending_questions=value.session.pending_questions,
                history=[{"role": item.role, "content": item.content} for item in value.history],
                timeout_s=self.timeout_s,
            )
        except AdapterError as exc:
            if exc.code == "model_output_invalid":
                raise AppError(
                    "model_output_invalid", "Clarification model output is invalid"
                ) from None
            raise

    async def assess_initial(self, value: SessionInput) -> SessionChange:
        value = SessionInput.model_validate(value)
        session = value.session
        if (
            value.history
            or session.run_id is not None
            or session.revision != 1
            or session.brief_version != 1
            or session.clarification_round != 0
            or session.status not in {"ask", "confirm"}
        ):
            raise AppError("invalid_state", "Initial candidate is not a new session")
        judgment = await self._model(
            value, session.query, session.brief_draft, session.source_selection
        )
        return self._candidate(
            value,
            self._decide(session.brief_draft, judgment),
            judgment,
            session.query,
            "initial",
            initial=True,
        )

    async def assess_round(
        self,
        value: SessionInput,
        answer: str,
        *,
        brief_patch: PartialResearchBrief | None = None,
        source_selection: SourceSelection | None = None,
    ) -> SessionChange:
        value = SessionInput.model_validate(value)
        self._clarifiable(value.session, "ask")
        answer = self._answer(answer)
        patch = (
            PartialResearchBrief.model_validate(brief_patch)
            if brief_patch is not None
            else PartialResearchBrief()
        )
        draft = PartialResearchBrief.model_validate(
            value.session.brief_draft.model_dump() | patch.model_dump()
        )
        selection = (
            SourceSelection.model_validate(source_selection)
            if source_selection is not None
            else value.session.source_selection
        )
        at_limit = value.session.clarification_round >= self.max_rounds
        if at_limit:
            judgment = ClarifyAssessment(
                missing_fields=[
                    field
                    for field in value.session.missing_fields
                    if field not in patch.model_fields_set
                ],
                questions=[],
                brief_patch=patch,
                assumptions=[],
                field_reasons={},
            )
        else:
            judgment = await self._model(value, answer, draft, selection)
        decision = self._decide(draft, judgment, model_called=not at_limit)
        if at_limit and not patch.model_fields_set:
            decision = BriefDecision(
                status="ask",
                draft=decision.draft,
                missing_fields=value.session.missing_fields,
                questions=["自动澄清已达到上限，请通过 brief_patch 明确补充或修改任务书字段。"],
            )
        return self._candidate(value, decision, judgment, answer, "answer", selection=selection)

    async def assess_rejection(
        self, value: SessionInput, feedback: str, *, source_selection: SourceSelection | None = None
    ) -> SessionChange:
        value = SessionInput.model_validate(value)
        self._clarifiable(value.session, "confirm")
        feedback = self._answer(feedback)
        selection = (
            SourceSelection.model_validate(source_selection)
            if source_selection is not None
            else value.session.source_selection
        )
        if value.session.clarification_round >= self.max_rounds:
            judgment = ClarifyAssessment(
                missing_fields=[], questions=[], brief_patch={}, assumptions=[], field_reasons={}
            )
            decision = BriefDecision(
                status="ask",
                draft=value.session.brief_draft,
                missing_fields=[],
                questions=["自动澄清已达到上限，请通过 brief_patch 明确提交任务书修改。"],
            )
        else:
            judgment = await self._model(value, feedback, value.session.brief_draft, selection)
            assessed = self._decide(value.session.brief_draft, judgment)
            decision = BriefDecision(
                status="ask",
                draft=assessed.draft,
                missing_fields=assessed.missing_fields,
                questions=assessed.questions or ["还有哪些研究范围或任务书字段需要调整？"],
            )
        return self._candidate(
            value, decision, judgment, feedback, "rejection", selection=selection
        )

    async def validate_confirmation(
        self, session: SessionState, expected_brief_version: int
    ) -> ValidatedFrozenInput:
        session = SessionState.model_validate(session)
        self._clarifiable(session, "confirm")
        if (
            type(expected_brief_version) is not int
            or expected_brief_version != session.brief_version
        ):
            raise AppError("stale_brief", "Read the current brief before confirming")
        if session.pending_questions or session.missing_fields:
            raise AppError("invalid_brief", "Brief has unresolved clarification gaps")
        try:
            brief = ResearchBrief.model_validate(session.brief_draft.model_dump())
        except ValidationError:
            raise AppError("invalid_brief", "Brief fields are incomplete") from None
        return ValidatedFrozenInput(
            owner_id=session.owner_id,
            session_id=session.session_id,
            expected_revision=session.revision,
            expected_brief_version=session.brief_version,
            research_brief=brief,
            source_selection=session.source_selection,
            confirmed_by=session.owner_id,
            origin="http",
        )

    @staticmethod
    def _decide(draft, judgment, *, model_called=True):
        try:
            return assess_brief(draft, judgment)
        except ValidationError:
            raise AppError(
                "model_output_invalid" if model_called else "invalid_brief",
                "Combined brief fields violate their bounds",
            ) from None

    @staticmethod
    def _clarifiable(session, status):
        if session.status != status or session.run_id is not None:
            raise AppError("invalid_session_state", f"Session must be {status}")

    @staticmethod
    def _answer(answer):
        if type(answer) is not str or not answer.strip() or len(answer) > 16000:
            raise AppError("validation_error", "Content must contain 1..16000 characters")
        return answer.strip()

    def _candidate(
        self, value, decision, judgment, content, kind, *, initial=False, selection=None
    ):
        before = value.session
        now = self.clock.now_utc()
        version = before.brief_version if initial else before.brief_version + 1
        round_count = 0 if initial else before.clarification_round + 1
        selected = selection if selection is not None else before.source_selection
        session = SessionState.model_validate(
            before.model_dump()
            | {
                "status": decision.status,
                "brief_draft": decision.draft,
                "revision": before.revision if initial else before.revision + 1,
                "brief_version": version,
                "clarification_round": round_count,
                "clarification_limit_reached": round_count >= self.max_rounds,
                "pending_questions": decision.questions,
                "missing_fields": decision.missing_fields,
                "source_selection": selected,
                "updated_at": now,
            }
        )
        sequence = value.history[-1].sequence + 1 if value.history else 1
        response = (
            "\n".join(decision.questions)
            if decision.status == "ask"
            else "请审阅并明确确认研究任务书。"
        )
        messages = [
            Message(
                message_id=self.id_factory(),
                session_id=session.session_id,
                sequence=sequence + offset,
                role=role,
                kind=message_kind,
                content=text,
                assessment=judgment if role == "assistant" else None,
                brief_version=version,
                created_at=now,
            )
            for offset, (role, message_kind, text) in enumerate(
                [("user", kind, content), ("assistant", "assessment", response)]
            )
        ]
        brief = BriefRecord(
            session_id=session.session_id,
            version=version,
            content=decision.draft,
            source_selection=selected,
            frozen_at=None,
            confirmed_by=None,
            content_hash=None,
        )
        return SessionChange(session=session, messages=messages, brief=brief)
