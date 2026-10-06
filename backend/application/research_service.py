"""Entry use case: create a session, spawn the pipeline, and read back results."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from uuid import UUID

from application.errors import AppError
from application.orchestrator import Orchestrator
from application.ports import CancellationPort, StateStorePort
from application.records import FreezeCommit, SessionChange, SessionInput, ValidatedFrozenInput
from application.research_inputs import (
    ConfirmResearchInput,
    ResearchMessageInput,
    ResearchResponse,
    StartResearchInput,
)
from application.session_service import LegacySessionService, SessionService
from domain.research.ids import canonical_hash
from domain.research.models import (
    BriefRecord,
    Message,
    PartialResearchBrief,
    ResearchBrief,
    ResearchRun,
    SessionState,
    SourceSelection,
)
from domain.research.state import Checkpoint, PipelineState

# Pipeline phases, in order, for recovering the latest phase from snapshots.
_PHASES = ["plan", "research", "analyze", "write", "review", "done"]


class LegacyResearchService:
    """Mediates session_service (clarify) and orchestrator (pipeline)."""

    def __init__(
        self,
        sessions: LegacySessionService,
        orchestrator: Orchestrator,
        store: StateStorePort,
        cancel: CancellationPort,
    ) -> None:
        self._sessions = sessions
        self._orchestrator = orchestrator
        self._store = store
        self._cancel = cancel

    async def start(self, query: str = "") -> dict:
        """Create a session; clarify runs later via POST /messages.

        Args:
            query: The initial research request, seeded into the session brief.
        """
        session_id = uuid.uuid4().hex
        await self._sessions.create(session_id, query)
        return {"session_id": session_id, "status": "clarify"}

    def spawn_pipeline(self, session_id: str, brief: dict) -> asyncio.Task:
        """Spawn the pipeline as a background task (fire-and-forget)."""
        return asyncio.create_task(self._orchestrator.run(session_id, brief))

    def cancel(self, session_id: str) -> None:
        """Request cancellation; the orchestrator stops at the next phase boundary."""
        self._cancel.set_cancelled(session_id)

    async def get_report(self, session_id: str) -> dict | None:
        """Return the final report from the reports table, if present."""
        return await self._store.load_report(session_id)

    async def get_status(self, session_id: str) -> dict:
        """Return the current status by recovering the latest phase snapshot."""
        for phase in reversed(_PHASES):
            snapshot = await self._store.load_latest_snapshot(session_id, phase)
            if snapshot is not None:
                return {
                    "session_id": session_id,
                    "status": "done" if phase == "done" else "running",
                    "phase": phase,
                }
        status = await self._store.get_session_status(session_id)
        return {"session_id": session_id, "status": status or "clarify"}


@dataclass
class _RequestLease:
    reservation: object
    lock: asyncio.Lock


class ResearchService:
    """Mono transaction coordinator; no model I/O inside resource transactions."""

    def __init__(
        self,
        *,
        uow,
        research,
        requests,
        users,
        sessions: SessionService,
        clock,
        settings,
        id_factory=uuid.uuid4,
        wake=None,
        renewal_interval_s=30,
    ):
        self.uow, self.repository, self.requests, self.users = uow, research, requests, users
        self.sessions, self.clock, self.settings = sessions, clock, settings
        self.id_factory, self.wake = id_factory, wake
        self.renewal_interval_s = renewal_interval_s

    async def _sources(self, owner, selection, tx=None):
        # The typed KB registry/bridge arrives in T041/T050. Legacy in-memory KBs
        # are not valid mono resources and must never be silently accepted.
        if selection.knowledge_base_ids:
            raise AppError("knowledge_base_not_found", "Knowledge base not found")

    async def _owned(self, owner, session_id, tx=None, *, lock=False):
        session = await self.repository.get_session(owner, session_id, tx, for_update=lock)
        if session is None:
            raise AppError("session_not_found", "Session not found")
        return session

    async def _input(self, owner, session_id):
        async with self.uow.transaction() as tx:
            session = await self._owned(owner, session_id, tx, lock=True)
            messages = await self.repository.list_messages(owner, session_id, tx)
            return SessionInput(session=session, history=tuple(messages))

    @staticmethod
    def _version(session, version):
        if session.brief_version != version:
            raise AppError(
                "stale_brief",
                "Read the current brief before changing it",
                details={"current_brief_version": session.brief_version},
            )

    async def _renew(self, lease):
        while True:
            await asyncio.sleep(self.renewal_interval_s)
            async with lease.lock, self.uow.transaction() as tx:
                lease.reservation = await self.requests.renew(lease.reservation, tx)

    async def _request(self, owner, operation, key, value, work, *, resource=None):
        if resource is not None:
            await self._owned(owner, resource)
        async with self.uow.transaction() as tx:
            if await self.users.get_by_id(owner, tx) is None:
                raise AppError("unauthenticated", "User identity is unavailable")
            reservation = await self.requests.reserve(
                owner, operation, key, canonical_hash(value), tx
            )
        if reservation.state == "completed":
            if reservation.response_body is None:
                raise AppError("internal_error", "Cached response is unavailable")
            return ResearchResponse(
                status_code=reservation.response_status, body=reservation.response_body
            )
        lease = _RequestLease(reservation, asyncio.Lock())
        worker = asyncio.create_task(work(lease))
        renewer = asyncio.create_task(self._renew(lease))
        succeeded = False
        try:
            done, _ = await asyncio.wait((worker, renewer), return_when=asyncio.FIRST_COMPLETED)
            if worker in done:
                result = await worker
                succeeded = True
                return result
            await renewer  # A failed renewal aborts in-flight model/candidate work.
            raise AppError("request_in_progress", "Request lease ended", retryable=True)
        finally:
            for task in (worker, renewer):
                if not task.done():
                    task.cancel()
            await asyncio.gather(worker, renewer, return_exceptions=True)
            if not succeeded:
                try:
                    async with self.uow.transaction() as tx:
                        await self.requests.release(lease.reservation, tx)
                except Exception:  # noqa: BLE001 -- cleanup must not mask the original failure
                    logging.getLogger(__name__).warning("request_reservation_release_failed")

    @staticmethod
    def _clarify_response(change):
        session = change.session
        body = {
            "session_id": str(session.session_id),
            "status": session.status,
            "brief_version": session.brief_version,
            "clarification_round": session.clarification_round,
            "clarification_limit_reached": session.clarification_limit_reached,
            "source_selection": session.source_selection.model_dump(mode="json"),
        }
        if session.status == "ask":
            body.update(
                questions=session.pending_questions,
                missing_fields=session.missing_fields,
                brief_draft=session.brief_draft.model_dump(mode="json"),
            )
        else:
            body["research_brief"] = ResearchBrief.model_validate(
                session.brief_draft.model_dump()
            ).model_dump(mode="json")
        return body

    async def _save_change(self, lease, change, expected_revision, status):
        body = self._clarify_response(change)
        async with lease.lock, self.uow.transaction() as tx:
            await self.repository.commit_session_change(expected_revision, change, tx)
            await self.requests.complete(
                lease.reservation, status, body, tx, resource_id=change.session.session_id
            )
        return ResearchResponse(status_code=status, body=body)

    def _new_session(self, owner, query, selection, draft):
        now = self.clock.now_utc()
        return SessionState(
            session_id=self.id_factory(),
            owner_id=owner,
            query=query,
            status="ask",
            revision=1,
            brief_draft=draft,
            brief_version=1,
            pending_questions=[],
            missing_fields=[],
            clarification_round=0,
            clarification_limit_reached=False,
            source_selection=selection,
            run_id=None,
            failure=None,
            created_at=now,
            updated_at=now,
        )

    async def start(self, owner: UUID, request: StartResearchInput, key: str) -> ResearchResponse:
        request = StartResearchInput.model_validate(request)

        async def work(lease):
            await self._sources(owner, request.selection)
            draft = (
                PartialResearchBrief(task_type=request.task_type)
                if request.task_type
                else PartialResearchBrief()
            )
            initial = SessionInput(
                session=self._new_session(owner, request.query, request.selection, draft),
                history=(),
            )
            change = await self.sessions.assess_initial(initial)
            return await self._save_change(lease, change, 0, 201)

        return await self._request(
            owner, "research:create", key, request.model_dump(mode="json"), work
        )

    async def message(
        self, owner: UUID, session_id: UUID, request: ResearchMessageInput, key: str
    ) -> ResearchResponse:
        request = ResearchMessageInput.model_validate(request)

        async def work(lease):
            value = await self._input(owner, session_id)
            self._version(value.session, request.brief_version)
            await self._sources(owner, request.source_selection or value.session.source_selection)
            change = await self.sessions.assess_round(
                value,
                request.content,
                brief_patch=request.brief_patch,
                source_selection=request.source_selection,
            )
            return await self._save_change(lease, change, value.session.revision, 200)

        return await self._request(
            owner,
            f"research:{session_id}:message",
            key,
            request.model_dump(mode="json"),
            work,
            resource=session_id,
        )

    async def start_frozen(
        self, owner: UUID, brief: ResearchBrief, selection: SourceSelection, key: str
    ) -> ResearchResponse:
        """Trusted CLI intake shares confirmation validation and never calls a model."""
        brief = ResearchBrief.model_validate(brief)
        selection = SourceSelection.model_validate(selection)

        async def work(lease):
            await self._sources(owner, selection)
            session = SessionState.model_validate(
                self._new_session(
                    owner,
                    brief.decision_goal,
                    selection,
                    PartialResearchBrief.model_validate(brief.model_dump()),
                ).model_dump()
                | {"status": "confirm"}
            )
            now = self.clock.now_utc()
            message = Message(
                message_id=self.id_factory(),
                session_id=session.session_id,
                sequence=1,
                role="user",
                kind="initial",
                content=f"CLI submitted frozen brief {canonical_hash(brief)}.",
                assessment=None,
                brief_version=1,
                created_at=now,
            )
            initial = SessionChange(
                session=session,
                messages=[message],
                brief=BriefRecord(
                    session_id=session.session_id,
                    version=1,
                    content=brief,
                    source_selection=selection,
                    frozen_at=None,
                    confirmed_by=None,
                    content_hash=None,
                ),
            )
            value = SessionInput(session=session, history=(message,))
            confirmed = await self.sessions.validate_confirmation(session, 1)
            confirmed = ValidatedFrozenInput.model_validate(
                confirmed.model_dump() | {"origin": "cli"}
            )
            commit = self._freeze(value, confirmed)
            body = self._ready(commit.session, commit.run)
            async with lease.lock, self.uow.transaction() as tx:
                await self.repository.commit_session_change(0, initial, tx)
                await self._sources(owner, selection, tx)
                await self.repository.freeze_and_create_run(
                    commit, tx, queue_limit=self.settings.owner_queue_limit
                )
                await self.requests.complete(
                    lease.reservation, 202, body, tx, resource_id=session.session_id
                )
            self._wake_runner()
            return ResearchResponse(status_code=202, body=body)

        return await self._request(
            owner,
            "research:create_frozen",
            key,
            {
                "research_brief": brief.model_dump(mode="json"),
                "source_selection": selection.model_dump(mode="json"),
            },
            work,
        )

    @staticmethod
    def _ready(session, run):
        return {
            "session_id": str(session.session_id),
            "run_id": str(run.run_id),
            "status": "ready",
            "brief_version": session.brief_version,
            "sse_url": f"/research/{session.session_id}/events",
        }

    def _wake_runner(self):
        if self.wake:
            try:
                self.wake()
            except Exception:  # noqa: BLE001 -- persisted acceptance survives a missed wake
                logging.getLogger(__name__).warning("runner_wake_failed")

    def _freeze(self, value, validated):
        now, run_id = self.clock.now_utc(), self.id_factory()
        session = SessionState.model_validate(
            value.session.model_dump()
            | {
                "status": "ready",
                "revision": value.session.revision + 1,
                "run_id": run_id,
                "updated_at": now,
            }
        )
        brief = BriefRecord(
            session_id=session.session_id,
            version=session.brief_version,
            content=validated.research_brief,
            source_selection=session.source_selection,
            frozen_at=now,
            confirmed_by=validated.confirmed_by,
            content_hash=canonical_hash(validated.research_brief),
        )
        config = self.settings.run_config_snapshot(
            categories=session.source_selection.categories,
            knowledge_base_ids=[str(item) for item in session.source_selection.knowledge_base_ids],
        )
        state = PipelineState.initial(
            session_id=session.session_id,
            run_id=run_id,
            brief_version=session.brief_version,
            research_brief=brief.content,
            source_selection=session.source_selection,
            config=config,
        )
        run = ResearchRun(
            run_id=run_id,
            session_id=session.session_id,
            brief_version=session.brief_version,
            brief_hash=brief.content_hash,
            status="ready",
            phase="plan",
            attempt_count=0,
            checkpoint_seq=1,
            cancel_requested_at=None,
            lease_owner=None,
            lease_token=0,
            lease_expires_at=None,
            resume_allowed=False,
            failure=None,
            config_snapshot=config,
            created_at=now,
            started_at=None,
            finished_at=None,
        )
        checkpoint = Checkpoint(
            snapshot_id=self.id_factory(),
            run_id=run_id,
            seq=1,
            schema_version=1,
            phase="plan",
            state=state,
            state_hash=canonical_hash(state),
            created_at=now,
        )
        message = Message(
            message_id=self.id_factory(),
            session_id=session.session_id,
            sequence=value.history[-1].sequence + 1 if value.history else 1,
            role="user",
            kind="confirmation",
            content=f"Explicit {validated.origin} confirmation of brief version {session.brief_version}.",
            assessment=None,
            brief_version=session.brief_version,
            created_at=now,
        )
        return FreezeCommit(
            expected_revision=value.session.revision,
            session=session,
            brief=brief,
            run=run,
            checkpoint=checkpoint,
            messages=[message],
        )

    async def confirm(
        self, owner: UUID, session_id: UUID, request: ConfirmResearchInput, key: str
    ) -> ResearchResponse:
        request = ConfirmResearchInput.model_validate(request)

        async def work(lease):
            value = await self._input(owner, session_id)
            self._version(value.session, request.brief_version)
            if not request.accepted:
                await self._sources(
                    owner, request.source_selection or value.session.source_selection
                )
                change = await self.sessions.assess_rejection(
                    value, request.feedback, source_selection=request.source_selection
                )
                return await self._save_change(lease, change, value.session.revision, 200)
            validated = (
                None
                if value.session.run_id
                else await self.sessions.validate_confirmation(value.session, request.brief_version)
            )
            commit = self._freeze(value, validated) if validated else None
            async with lease.lock, self.uow.transaction() as tx:
                current = await self._owned(owner, session_id, tx, lock=True)
                self._version(current, request.brief_version)
                if current.run_id:
                    run = await self.repository.get_run(owner, current.run_id, tx)
                    if run is None:
                        raise AppError("service_not_ready", "Accepted run is unavailable")
                    body = self._ready(current, run)
                else:
                    await self._sources(owner, current.source_selection, tx)
                    run = await self.repository.freeze_and_create_run(
                        commit, tx, queue_limit=self.settings.owner_queue_limit
                    )
                    body = self._ready(commit.session, run)
                await self.requests.complete(
                    lease.reservation, 202, body, tx, resource_id=session_id
                )
            self._wake_runner()
            return ResearchResponse(status_code=202, body=body)

        return await self._request(
            owner,
            f"research:{session_id}:confirm",
            key,
            request.model_dump(mode="json"),
            work,
            resource=session_id,
        )
