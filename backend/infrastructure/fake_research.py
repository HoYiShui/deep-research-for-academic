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

from application.errors import AppError
from application.records import FreezeCommit, IdempotencyRecord, SessionChange, User
from domain.research.models import BriefRecord, Message, ResearchRun, SessionState
from domain.research.state import Checkpoint


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
    requests: dict[tuple[UUID, str, str], IdempotencyRecord] = field(default_factory=dict)


@dataclass
class _Tx:
    transaction_id: UUID
    owner: FakeResearchDatabase
    data: _Data
    active: bool = True


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
        return self.data(tx)


class _Users:
    def __init__(self, db):
        self.db = db

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

    async def freeze_and_create_run(self, commit: FreezeCommit, tx) -> ResearchRun:
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
