"""Deterministic atomic repository double, not a PostgreSQL substitute.

Every repository uses a single staged transaction snapshot. Reads without a
transaction see committed data; all writes require a live, same-store handle.
"""

from __future__ import annotations

import asyncio
import copy
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from application.errors import AppError
from application.records import (
    ClaimedRun,
    DevelopmentUser,
    FreezeCommit,
    IdempotencyRecord,
    SessionChange,
    User,
)
from domain.research.facts import FinalReport
from domain.research.models import BriefRecord, Failure, Message, ResearchRun, SessionState, Text
from domain.research.state import Checkpoint
from infrastructure.storage.run_leases import RunLeases
from infrastructure.storage.run_publication import RunPublication
from infrastructure.storage.run_termination import RunTermination


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._wall = start or datetime(2026, 10, 5, tzinfo=UTC)
        if self._wall.tzinfo is None or self._wall.utcoffset() is None:
            raise ValueError("Fake clock requires aware time")
        self._wall = self._wall.astimezone(UTC)
        self._mono = 0.0

    def now_utc(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Monotonic clock cannot move backwards or become nonfinite")
        self._mono += seconds
        self.shift_wall(seconds)

    def shift_wall(self, seconds: float) -> None:
        self._wall += timedelta(seconds=seconds)


@dataclass
class _Data:
    users: dict[UUID, User] = field(default_factory=dict)
    sessions: dict[UUID, SessionState] = field(default_factory=dict)
    briefs: dict[tuple[UUID, int], BriefRecord] = field(default_factory=dict)
    messages: dict[UUID, list[Message]] = field(default_factory=dict)
    runs: dict[UUID, ResearchRun] = field(default_factory=dict)
    checkpoints: dict[tuple[UUID, int], Checkpoint] = field(default_factory=dict)
    reports: dict[UUID, FinalReport] = field(default_factory=dict)
    requests: dict[tuple[UUID, str, str], IdempotencyRecord] = field(default_factory=dict)


@dataclass
class _Tx:
    transaction_id: UUID
    owner: FakeResearchDatabase
    data: _Data
    active: bool = True
    read_only: bool = False


class FakeResearchDatabase:
    def __init__(self, clock: FakeClock | None = None):
        self.clock = clock or FakeClock()
        self._data = _Data()
        self._lock = asyncio.Lock()
        self.users = _Users(self)
        self.research = _Research(self)
        self.requests = _Requests(self)

    @asynccontextmanager
    async def transaction(self):
        async with self._lock:
            tx = _Tx(uuid4(), self, copy.deepcopy(self._data))
            try:
                yield tx
                self._data = tx.data
            finally:
                tx.active = False

    @asynccontextmanager
    async def snapshot(self):
        async with self._lock:
            tx = _Tx(uuid4(), self, copy.deepcopy(self._data), read_only=True)
        try:
            yield tx
        finally:
            tx.active = False

    def data(self, tx=None) -> _Data:
        if tx is None:
            return self._data
        if not isinstance(tx, _Tx) or tx.owner is not self:
            raise ValueError("Transaction handle is foreign")
        if not tx.active:
            raise ValueError("Transaction handle is closed")
        return tx.data

    def write_data(self, tx) -> _Data:
        if tx is None:
            raise ValueError("A live transaction is required for writes")
        data = self.data(tx)
        if tx.read_only:
            raise ValueError("A read-only snapshot cannot write")
        return data


class _Users:
    def __init__(self, db):
        self.db = db

    async def ensure_development(self, user: DevelopmentUser, tx) -> DevelopmentUser:
        user = DevelopmentUser.model_validate(user)
        data = self.db.write_data(tx)
        if user.user_id not in data.users and not any(
            item.email == user.email for item in data.users.values()
        ):
            data.users[user.user_id] = copy.deepcopy(user)
        current = data.users.get(user.user_id)
        try:
            return DevelopmentUser.model_validate(current.model_dump() if current else None)
        except ValidationError:
            raise AppError(
                "service_not_ready", "Reserved development identity is unavailable"
            ) from None

    async def create(self, user: User, tx) -> None:
        user = User.model_validate(user)
        data = self.db.write_data(tx)
        if user.user_id in data.users or any(
            item.email == user.email for item in data.users.values()
        ):
            raise AppError("email_already_registered", "User identity already exists")
        data.users[user.user_id] = copy.deepcopy(user)

    async def get_by_id(self, user_id: UUID, tx=None) -> User | None:
        return copy.deepcopy(self.db.data(tx).users.get(user_id))

    async def get_by_email(self, email: str, tx=None) -> User | None:
        return copy.deepcopy(
            next(
                (
                    user
                    for user in self.db.data(tx).users.values()
                    if user.email == email.strip().lower()
                ),
                None,
            )
        )


class _Research:
    def __init__(self, db):
        self.db = db

    async def get_session(
        self, owner: UUID, session_id: UUID, tx=None, *, for_update=False
    ) -> SessionState | None:
        if for_update and tx is None:
            raise ValueError("Row lock requires transaction")
        session = self.db.data(tx).sessions.get(session_id)
        return copy.deepcopy(session) if session is not None and session.owner_id == owner else None

    @staticmethod
    def _messages(data, session_id, incoming):
        previous = data.messages.get(session_id, [])
        if [message.sequence for message in incoming] != list(
            range(len(previous) + 1, len(previous) + len(incoming) + 1)
        ):
            raise ValueError("Messages must append consecutive sequences")
        all_ids = {
            message.message_id for messages in data.messages.values() for message in messages
        }
        if any(message.message_id in all_ids for message in incoming):
            raise ValueError("Message ID already exists")
        return previous + copy.deepcopy(incoming)

    async def commit_session_change(
        self, expected_revision: int, change: SessionChange, tx
    ) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected revision must be a nonnegative integer")
        change = SessionChange.model_validate(change)
        data = self.db.write_data(tx)
        session = change.session
        if session.owner_id not in data.users:
            raise ValueError("Session owner is not registered")
        old = data.sessions.get(session.session_id)
        if old is not None and old.owner_id != session.owner_id:
            raise AppError("session_not_found", "Session not found")
        actual_revision = old.revision if old else 0
        if actual_revision != expected_revision or session.revision != expected_revision + 1:
            raise AppError("stale_resource", "Session revision changed")
        if session.status not in {"ask", "confirm"}:
            raise AppError("invalid_session_state", "Clarification candidate must ask or confirm")
        if session.brief_version != (old.brief_version + 1 if old else 1):
            raise AppError("stale_brief", "Processed clarification must advance brief version")
        if old is not None and (old.query != session.query or old.created_at != session.created_at):
            raise AppError("invalid_state", "Candidate changes immutable session fields")
        if old is not None and old.run_id is not None:
            raise AppError("invalid_session_state", "Frozen session cannot change brief")
        key = (session.session_id, change.brief.version)
        previous_brief = data.briefs.get(key)
        if previous_brief is not None:
            raise AppError("stale_brief", "Brief version already exists")
        messages = self._messages(data, session.session_id, change.messages)
        data.sessions[session.session_id] = copy.deepcopy(session)
        data.briefs[key] = copy.deepcopy(change.brief)
        data.messages[session.session_id] = messages

    async def list_messages(self, owner: UUID, session_id: UUID, tx=None) -> list[Message]:
        if await self.get_session(owner, session_id, tx) is None:
            return []
        return copy.deepcopy(self.db.data(tx).messages.get(session_id, []))

    async def load_brief(
        self, owner: UUID, session_id: UUID, version: int, tx=None
    ) -> BriefRecord | None:
        if await self.get_session(owner, session_id, tx) is None:
            return None
        return copy.deepcopy(self.db.data(tx).briefs.get((session_id, version)))

    async def freeze_and_create_run(
        self, commit: FreezeCommit, tx, *, queue_limit=20
    ) -> ResearchRun:
        commit = FreezeCommit.model_validate(commit)
        data = self.db.write_data(tx)
        current = await self.get_session(commit.session.owner_id, commit.session.session_id, tx)
        if current is None:
            raise AppError("session_not_found", "Session not found")
        if current.revision != commit.expected_revision:
            raise AppError("stale_resource", "Session revision changed")
        if current.brief_version != commit.brief.version:
            raise AppError("stale_brief", "Brief version changed")
        if current.status != "confirm" or current.run_id is not None:
            raise AppError("invalid_session_state", "Session is not awaiting confirmation")
        self._ready_capacity(current.owner_id, tx, queue_limit=queue_limit)
        if current.brief_draft.model_dump() != commit.brief.content.model_dump():
            raise AppError("stale_brief", "Confirmation changes the assessed brief")
        if current.source_selection != commit.brief.source_selection:
            raise AppError("stale_brief", "Confirmation changes the assessed sources")
        if any(run.session_id == current.session_id for run in data.runs.values()):
            raise AppError("invalid_session_state", "Session already has a run")
        if commit.run.run_id in data.runs or commit.checkpoint.snapshot_id in {
            checkpoint.snapshot_id for checkpoint in data.checkpoints.values()
        }:
            raise ValueError("Run/checkpoint identity already exists")
        messages = self._messages(data, current.session_id, commit.messages)
        data.sessions[current.session_id] = copy.deepcopy(commit.session)
        data.briefs[(current.session_id, commit.brief.version)] = copy.deepcopy(commit.brief)
        data.runs[commit.run.run_id] = copy.deepcopy(commit.run)
        data.checkpoints[(commit.run.run_id, 1)] = copy.deepcopy(commit.checkpoint)
        data.messages[current.session_id] = messages
        return copy.deepcopy(commit.run)

    async def get_run(self, owner: UUID, run_id: UUID, tx=None) -> ResearchRun | None:
        run = self.db.data(tx).runs.get(run_id)
        if run is None or await self.get_session(owner, run.session_id, tx) is None:
            return None
        return copy.deepcopy(run)

    async def load_checkpoint(
        self, owner: UUID, run_id: UUID, seq: int, tx=None
    ) -> Checkpoint | None:
        if await self.get_run(owner, run_id, tx) is None:
            return None
        return copy.deepcopy(self.db.data(tx).checkpoints.get((run_id, seq)))

    async def load_latest_checkpoint(self, owner, run_id, tx=None):
        run = await self.get_run(owner, run_id, tx)
        return await self.load_checkpoint(owner, run_id, run.checkpoint_seq, tx) if run else None

    async def claim_run(
        self, worker, tx, *, owner=None, run_id=None, lease_s=90, global_limit=2, owner_limit=1
    ):
        worker = TypeAdapter(Text).validate_python(worker)
        if any(
            type(value) is not int or value <= 0 for value in (lease_s, global_limit, owner_limit)
        ):
            raise ValueError("Lease and capacity settings must be positive integers")
        if run_id is not None and owner is None:
            raise ValueError("A targeted CLI claim requires an owner")
        data, now = self.db.write_data(tx), self.db.clock.now_utc()
        active = [
            run
            for run in data.runs.values()
            if run.status in {"running", "cancelling"}
            and run.lease_expires_at is not None
            and run.lease_expires_at > now
        ]
        if len(active) >= global_limit:
            return None
        for run in sorted(data.runs.values(), key=lambda item: (item.created_at, str(item.run_id))):
            session = data.sessions[run.session_id]
            if (
                run.status != "ready"
                or session.status != "ready"
                or run.cancel_requested_at is not None
            ):
                continue
            if (
                owner is not None
                and session.owner_id != owner
                or run_id is not None
                and run.run_id != run_id
            ):
                continue
            if (
                sum(data.sessions[item.session_id].owner_id == session.owner_id for item in active)
                >= owner_limit
            ):
                continue
            updated = ResearchRun.model_validate(
                run.model_dump()
                | {
                    "status": "running",
                    "attempt_count": run.attempt_count + 1,
                    "lease_owner": worker,
                    "lease_token": run.lease_token + 1,
                    "lease_expires_at": now + timedelta(seconds=lease_s),
                    "started_at": run.started_at or now,
                    "finished_at": None,
                    "failure": None,
                    "resume_allowed": False,
                }
            )
            data.runs[run.run_id] = updated
            data.sessions[run.session_id] = SessionState.model_validate(
                session.model_dump()
                | {
                    "status": "running",
                    "revision": session.revision + 1,
                    "failure": None,
                    "updated_at": now,
                }
            )
            return ClaimedRun(owner_id=session.owner_id, run=copy.deepcopy(updated))
        return None

    async def renew_lease(self, claimed, tx, *, lease_s=90):
        claimed = ClaimedRun.model_validate(claimed)
        if type(lease_s) is not int or lease_s <= 0:
            raise ValueError("Lease must be positive")
        data, now = self.db.write_data(tx), self.db.clock.now_utc()
        current = await self.get_run(claimed.owner_id, claimed.run.run_id, tx)
        if current is None:
            raise AppError("session_not_found", "Session not found")
        if (
            current.status not in {"running", "cancelling"}
            or current.lease_owner != claimed.run.lease_owner
            or current.lease_token != claimed.run.lease_token
            or current.lease_expires_at is None
            or current.lease_expires_at <= now
        ):
            raise AppError("stale_resource", "Run lease is no longer owned")
        updated = ResearchRun.model_validate(
            current.model_dump() | {"lease_expires_at": now + timedelta(seconds=lease_s)}
        )
        data.runs[current.run_id] = updated
        return ClaimedRun(owner_id=claimed.owner_id, run=copy.deepcopy(updated))

    async def check_run_lease(self, claimed, tx):
        claimed = ClaimedRun.model_validate(claimed)
        self.db.write_data(tx)
        current = await self.get_run(claimed.owner_id, claimed.run.run_id, tx)
        if current is None:
            raise AppError("session_not_found", "Session not found")
        if (
            current.status not in {"running", "cancelling"}
            or current.lease_owner != claimed.run.lease_owner
            or current.lease_token != claimed.run.lease_token
            or current.lease_expires_at is None
            or current.lease_expires_at <= self.db.clock.now_utc()
        ):
            raise AppError("stale_resource", "Run lease is no longer owned")
        return current

    async def commit_checkpoint(self, claimed, expected_seq, checkpoint, tx):
        claimed = ClaimedRun.model_validate(claimed)
        checkpoint = Checkpoint.model_validate(checkpoint)
        data, now = self.db.write_data(tx), self.db.clock.now_utc()
        run = await self.get_run(claimed.owner_id, claimed.run.run_id, tx)
        if run is None:
            raise AppError("session_not_found", "Session not found")
        if (
            run.status not in {"running", "cancelling"}
            or run.lease_owner != claimed.run.lease_owner
            or run.lease_token != claimed.run.lease_token
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
        ):
            raise AppError("stale_resource", "Run lease is no longer owned")
        session = data.sessions[run.session_id]
        if session.run_id != run.run_id or session.status != run.status:
            raise AppError("invalid_state", "Run and Session status are inconsistent")
        previous = await self.load_latest_checkpoint(claimed.owner_id, run.run_id, tx)
        RunLeases._checkpoint_input(run, session, previous, checkpoint, expected_seq)
        if checkpoint.snapshot_id in {item.snapshot_id for item in data.checkpoints.values()}:
            raise AppError("stale_resource", "Checkpoint identity exists")
        data.checkpoints[(run.run_id, checkpoint.seq)] = copy.deepcopy(checkpoint)
        updated = ResearchRun.model_validate(
            run.model_dump()
            | {
                "checkpoint_seq": checkpoint.seq,
                "phase": checkpoint.phase,
            }
        )
        data.runs[run.run_id] = updated
        data.sessions[run.session_id] = SessionState.model_validate(
            session.model_dump()
            | {
                "revision": session.revision + 1,
                "updated_at": now,
            }
        )
        return ClaimedRun(owner_id=claimed.owner_id, run=copy.deepcopy(updated))

    async def publish_report(self, claimed, expected_seq, checkpoint, tx):
        point = Checkpoint.model_validate(checkpoint)
        session, run = await self._owned_lease(claimed, tx)
        previous = await self.load_latest_checkpoint(claimed.owner_id, run.run_id, tx)
        RunPublication._publication_input(run, session, previous, point, expected_seq)
        data = self.db.write_data(tx)
        if run.run_id in data.reports or any(
            item.snapshot_id == point.snapshot_id for item in data.checkpoints.values()
        ):
            raise AppError("stale_resource", "Publication identity already exists")
        data.reports[run.run_id] = copy.deepcopy(point.state.final_report)
        data.checkpoints[(run.run_id, point.seq)] = copy.deepcopy(point)
        updated = ResearchRun.model_validate(
            run.model_dump() | {"phase": "done", "checkpoint_seq": point.seq}
        )
        return self._terminal(session, updated, "completed", tx)[1]

    async def load_report(self, owner, run_id, tx=None):
        if await self.get_run(owner, run_id, tx) is None:
            return None
        return copy.deepcopy(self.db.data(tx).reports.get(run_id))

    def _ready_capacity(self, owner, tx, *, queue_limit=20):
        RunLeases._positive(queue_limit)
        data = self.db.write_data(tx)
        count = sum(
            run.status == "ready" and data.sessions[run.session_id].owner_id == owner
            for run in data.runs.values()
        )
        if count >= queue_limit:
            raise AppError("rate_limited", "Research queue is full", retryable=True)

    def _terminal(self, session, run, status, tx, failure=None):
        data, now = self.db.write_data(tx), self.db.clock.now_utc()
        updated_session = SessionState.model_validate(
            session.model_dump()
            | {
                "status": status,
                "failure": failure,
                "revision": session.revision + 1,
                "updated_at": now,
            }
        )
        data.sessions[session.session_id] = updated_session
        updated_run = None
        if run:
            updated_run = ResearchRun.model_validate(
                run.model_dump()
                | {
                    "status": status,
                    "failure": failure,
                    "resume_allowed": bool(failure and failure.resume_allowed),
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "finished_at": now,
                }
            )
            data.runs[run.run_id] = updated_run
        return copy.deepcopy(updated_session), copy.deepcopy(updated_run)

    async def request_cancel(self, owner, session_id, tx):
        data, now = self.db.write_data(tx), self.db.clock.now_utc()
        session = await self.get_session(owner, session_id, tx)
        if session is None:
            raise AppError("session_not_found", "Session not found")
        if session.status == "completed":
            raise AppError("invalid_session_state", "Completed research cannot be cancelled")
        if session.status in {"cancelled", "cancelling"}:
            return session
        run = await self.get_run(owner, session.run_id, tx) if session.run_id else None
        if run:
            run = ResearchRun.model_validate(
                run.model_dump()
                | {
                    "cancel_requested_at": run.cancel_requested_at or now,
                    "resume_allowed": False,
                }
            )
            data.runs[run.run_id] = run
        if session.status in {"ask", "confirm", "failed"}:
            return self._terminal(session, run, "cancelled", tx)[0]
        if session.status not in {"ready", "running"} or run is None:
            raise AppError("invalid_session_state", "Research cannot be cancelled")
        updated = SessionState.model_validate(
            session.model_dump()
            | {
                "status": "cancelling",
                "revision": session.revision + 1,
                "updated_at": now,
            }
        )
        data.sessions[session_id] = updated
        data.runs[run.run_id] = ResearchRun.model_validate(
            run.model_dump() | {"status": "cancelling"}
        )
        return copy.deepcopy(updated)

    async def _owned_lease(self, claimed, tx):
        claimed = ClaimedRun.model_validate(claimed)
        original = await self.get_run(claimed.owner_id, claimed.run.run_id, tx)
        if original is None:
            raise AppError("session_not_found", "Session not found")
        if (
            original.status not in {"running", "cancelling"}
            or original.lease_owner != claimed.run.lease_owner
            or original.lease_token != claimed.run.lease_token
            or original.lease_expires_at is None
            or original.lease_expires_at <= self.db.clock.now_utc()
        ):
            raise AppError("stale_resource", "Run lease is no longer owned")
        return await self.get_session(claimed.owner_id, original.session_id, tx), original

    async def finish_cancelled(self, claimed, tx):
        session, run = await self._owned_lease(claimed, tx)
        if run.status != "cancelling" or run.cancel_requested_at is None:
            raise AppError("invalid_session_state", "Cancellation was not requested")
        return self._terminal(session, run, "cancelled", tx)[1]

    async def fail_run(self, claimed, failure, tx):
        session, run = await self._owned_lease(claimed, tx)
        failure = RunTermination._failure_input(run, failure)
        if await self.load_latest_checkpoint(claimed.owner_id, run.run_id, tx) is None:
            raise AppError("invalid_state", "Failure requires the last safe checkpoint")
        return self._terminal(session, run, "failed", tx, failure)[1]

    async def resume_run(self, owner, session_id, seq, config, tx, *, queue_limit=20):
        data, now = self.db.write_data(tx), self.db.clock.now_utc()
        session = await self.get_session(owner, session_id, tx)
        if session is None:
            raise AppError("session_not_found", "Session not found")
        run = await self.get_run(owner, session.run_id, tx) if session.run_id else None
        point = await self.load_latest_checkpoint(owner, run.run_id, tx) if run else None
        RunTermination._resume_input(session, run, seq, config, point)
        self._ready_capacity(owner, tx, queue_limit=queue_limit)
        updated = ResearchRun.model_validate(
            run.model_dump()
            | {
                "status": "ready",
                "failure": None,
                "resume_allowed": False,
                "finished_at": None,
                "lease_owner": None,
                "lease_expires_at": None,
            }
        )
        data.runs[run.run_id] = updated
        data.sessions[session_id] = SessionState.model_validate(
            session.model_dump()
            | {
                "status": "ready",
                "failure": None,
                "revision": session.revision + 1,
                "updated_at": now,
            }
        )
        return copy.deepcopy(updated)

    async def scan_interrupted(
        self, tx, *, queue_timeout_s=1800, limit=100, owner=None, run_id=None
    ):
        RunLeases._positive(queue_timeout_s)
        RunLeases._positive(limit)
        if run_id is not None and owner is None:
            raise ValueError("A targeted CLI scan requires an owner")
        data, now = self.db.write_data(tx), self.db.clock.now_utc()
        changed = []
        for run in sorted(
            data.runs.values(),
            key=lambda item: (data.sessions[item.session_id].updated_at, str(item.session_id)),
        ):
            session = data.sessions[run.session_id]
            if (owner is not None and session.owner_id != owner) or (
                run_id is not None and run.run_id != run_id
            ):
                continue
            if run.status in {"running", "cancelling"}:
                if run.lease_expires_at is not None and run.lease_expires_at > now:
                    continue
            elif (
                run.status != "ready"
                or (now - session.updated_at).total_seconds() < queue_timeout_s
            ):
                continue
            if run.status == "cancelling":
                result = self._terminal(session, run, "cancelled", tx)[1]
            else:
                point = await self.load_latest_checkpoint(session.owner_id, run.run_id, tx)
                resumable = bool(
                    point
                    and point.phase == run.phase
                    and point.state.brief_hash == run.brief_hash
                    and point.state.brief_version == run.brief_version
                    and point.state.session_id == session.session_id
                    and point.state.source_selection == session.source_selection
                    and point.state.run_metadata.config == run.config_snapshot
                )
                code = "interrupted" if run.status == "running" else "queue_timeout"
                failure = Failure(
                    code=code if resumable else "schema_incompatible",
                    dependency=None,
                    operation="recovery_scan",
                    phase=run.phase,
                    message="Run requires operator attention",
                    retryable=False,
                    resume_allowed=resumable,
                    attempt=run.attempt_count,
                    occurred_at=now,
                    details=None,
                )
                result = self._terminal(session, run, "failed", tx, failure)[1]
            changed.append(result)
            if len(changed) >= limit:
                break
        return changed


class _Requests:
    def __init__(self, db):
        self.db = db

    async def reserve(
        self, owner: UUID, operation: str, key: str, request_hash: str, tx, *, lease_s=120
    ):
        data = self.db.write_data(tx)
        if owner not in data.users:
            raise ValueError("Request owner is not registered")
        if type(lease_s) is not int or lease_s <= 0:
            raise ValueError("Operation lease must be positive")
        candidate = IdempotencyRecord(
            owner_id=owner,
            operation=operation,
            key=key,
            request_hash=request_hash,
            state="in_progress",
            lease_expires_at=self.db.clock.now_utc() + timedelta(seconds=lease_s),
            response_status=None,
            response_body=None,
            resource_id=None,
        )
        identity = (owner, candidate.operation, candidate.key)
        old = data.requests.get(identity)
        if old is not None:
            if old.request_hash != candidate.request_hash:
                raise AppError("idempotency_conflict", "Request key already binds a different body")
            if old.state == "completed":
                return copy.deepcopy(old)
            if old.lease_expires_at > self.db.clock.now_utc():
                raise AppError(
                    "request_in_progress", "Request is already processing", retryable=True
                )
            candidate = candidate.model_copy(update={"resource_id": old.resource_id})
        data.requests[identity] = copy.deepcopy(candidate)
        return candidate

    def _current(self, reservation, tx):
        data = self.db.write_data(tx)
        identity = (reservation.owner_id, reservation.operation, reservation.key)
        current = data.requests.get(identity)
        if (
            current is None
            or current.state != "in_progress"
            or current.request_hash != reservation.request_hash
            or current.lease_expires_at != reservation.lease_expires_at
            or current.lease_expires_at <= self.db.clock.now_utc()
        ):
            raise AppError(
                "request_in_progress", "Request reservation is no longer owned", retryable=True
            )
        return data, identity, current

    async def renew(self, reservation, tx, *, lease_s=120):
        data, identity, current = self._current(reservation, tx)
        if type(lease_s) is not int or lease_s <= 0:
            raise ValueError("Operation lease must be positive")
        renewed = IdempotencyRecord.model_validate(
            current.model_dump()
            | {"lease_expires_at": self.db.clock.now_utc() + timedelta(seconds=lease_s)}
        )
        data.requests[identity] = copy.deepcopy(renewed)
        return renewed

    async def complete(self, reservation, response_status, response_body, tx, *, resource_id=None):
        if type(response_status) is int and response_status >= 500:
            raise ValueError("Transient server failure must release reservation, not complete it")
        data, identity, current = self._current(reservation, tx)
        completed = IdempotencyRecord.model_validate(
            current.model_dump()
            | {
                "state": "completed",
                "response_status": response_status,
                "response_body": response_body,
                "resource_id": resource_id,
            }
        )
        data.requests[identity] = copy.deepcopy(completed)
        return completed

    async def release(self, reservation, tx):
        data, identity, _ = self._current(reservation, tx)
        del data.requests[identity]
