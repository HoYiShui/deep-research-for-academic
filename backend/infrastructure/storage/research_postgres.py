"""Mono-v1 repositories sharing one adapter-owned PostgreSQL transaction."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
from pydantic import ValidationError

from application.errors import AppError
from application.records import (
    DevelopmentUser,
    FreezeCommit,
    IdempotencyRecord,
    SessionChange,
    User,
)
from domain.ports import AdapterError
from domain.research.models import BriefRecord, Message, ResearchRun, SessionState
from domain.research.state import Checkpoint

SESSION_JSON = {"brief_draft", "pending_questions", "missing_fields", "source_selection", "failure"}
BRIEF_JSON = {"content", "source_selection"}
RUN_JSON = {"failure", "config_snapshot"}


def decode(model, row, json_fields):
    if row is None:
        return None
    try:
        data = {field: row[field] for field in model.model_fields}
        for field in json_fields:
            if isinstance(data[field], str):
                data[field] = json.loads(data[field])
        return model.model_validate(data)
    except (ValidationError, ValueError, KeyError):
        raise AdapterError(
            "postgres", "schema_incompatible", "Persisted record is invalid", False, "read"
        ) from None


def encode(record, json_fields):
    data = record.model_dump(mode="python")
    json_data = record.model_dump(mode="json")
    for field in json_fields:
        data[field] = (
            json.dumps(json_data[field], ensure_ascii=False, allow_nan=False)
            if data[field] is not None
            else None
        )
    return data


async def insert(conn, table, data, suffix=""):
    # Every table/column is an internal model or constant, never request input.
    columns = list(data)
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('$' + str(i + 1) for i in range(len(columns)))})"
    return await conn.fetchrow(sql + suffix, *data.values())


@dataclass
class _Transaction:
    transaction_id: UUID
    store: PostgresResearchStore
    _connection: asyncpg.Connection
    active: bool = True


class PostgresResearchStore:
    """Construct after explicit migration; caller owns pool lifecycle."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.users = _Users(self)
        self.research = _Research(self)
        self.requests = _Requests(self)

    @asynccontextmanager
    async def transaction(self):
        async with self.pool.acquire() as conn:
            tx = _Transaction(uuid4(), self, conn)
            try:
                async with conn.transaction():
                    yield tx
            except asyncpg.UniqueViolationError as exc:
                codes = {
                    "mono_briefs_pk": "stale_brief",
                    "mono_one_run_per_session": "invalid_session_state",
                    "mono_checkpoint_sequence": "stale_resource",
                    "mono_message_sequence": "invalid_state",
                    "mono_messages_pk": "invalid_state",
                    "mono_users_email_unique": "email_already_registered",
                }
                raise AppError(
                    codes.get(exc.constraint_name, "invalid_state"),
                    "Transaction identity constraint failed",
                ) from None
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
                raise AdapterError(
                    "postgres",
                    "dependency_unavailable",
                    "Transaction could not commit",
                    True,
                    "transaction",
                ) from None
            finally:
                tx.active = False

    def connection(self, tx):
        if not isinstance(tx, _Transaction) or tx.store is not self:
            raise ValueError("Transaction handle is foreign")
        if not tx.active:
            raise ValueError("Transaction handle is closed")
        return tx._connection

    @asynccontextmanager
    async def read(self, tx=None):
        try:
            if tx is not None:
                yield self.connection(tx)
            else:
                async with self.pool.acquire() as conn:
                    yield conn
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
            raise AdapterError(
                "postgres", "dependency_unavailable", "Storage read failed", True, "read"
            ) from None


class _Users:
    def __init__(self, store):
        self.store = store

    async def ensure_development(self, user: DevelopmentUser, tx) -> DevelopmentUser:
        user = DevelopmentUser.model_validate(user)
        conn = self.store.connection(tx)
        # DO NOTHING covers UUID and email collisions; never mutate an existing user.
        await insert(conn, "users", encode(user, set()), " ON CONFLICT DO NOTHING")
        current = await self.get_by_id(user.user_id, tx)
        try:
            return DevelopmentUser.model_validate(current.model_dump() if current else None)
        except ValidationError:
            raise AppError(
                "service_not_ready", "Reserved development identity is unavailable"
            ) from None

    async def create(self, user: User, tx) -> None:
        user = User.model_validate(user)
        try:
            await insert(self.store.connection(tx), "users", encode(user, set()))
        except asyncpg.UniqueViolationError:
            raise AppError("email_already_registered", "User identity already exists") from None

    async def get_by_id(self, user_id: UUID, tx=None) -> User | None:
        async with self.store.read(tx) as conn:
            return decode(
                User, await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id), set()
            )

    async def get_by_email(self, email: str, tx=None) -> User | None:
        async with self.store.read(tx) as conn:
            return decode(
                User,
                await conn.fetchrow("SELECT * FROM users WHERE email=$1", email.strip().lower()),
                set(),
            )


class _Research:
    def __init__(self, store):
        self.store = store

    async def get_session(
        self, owner: UUID, session_id: UUID, tx=None, *, for_update=False
    ) -> SessionState | None:
        if for_update and tx is None:
            raise ValueError("Row lock requires transaction")
        async with self.store.read(tx) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE owner_id=$1 AND session_id=$2"
                + (" FOR UPDATE" if for_update else ""),
                owner,
                session_id,
            )
            return decode(SessionState, row, SESSION_JSON)

    async def _append_messages(self, session_id, messages, conn):
        last = await conn.fetchval(
            "SELECT coalesce(max(sequence),0) FROM messages WHERE session_id=$1", session_id
        )
        if [message.sequence for message in messages] != list(
            range(last + 1, last + len(messages) + 1)
        ):
            raise AppError("invalid_state", "Messages must append consecutive sequences")
        for message in messages:
            await insert(conn, "messages", encode(message, {"assessment"}))

    @staticmethod
    def _immutable_session(old, new, allowed):
        before, after = old.model_dump(), new.model_dump()
        if any(before[key] != after[key] for key in before if key not in allowed):
            raise AppError("invalid_state", "Candidate changes immutable session fields")

    async def commit_session_change(
        self, expected_revision: int, change: SessionChange, tx
    ) -> None:
        change = SessionChange.model_validate(change)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected revision must be a nonnegative integer")
        conn = self.store.connection(tx)
        session = change.session
        if session.revision != expected_revision + 1:
            raise AppError("stale_resource", "Candidate revision is not the next revision")
        if session.status not in {"ask", "confirm"}:
            raise AppError("invalid_session_state", "Clarification candidate must ask or confirm")
        if expected_revision == 0:
            if session.brief_version != 1:
                raise AppError("stale_brief", "Initial brief must start at version one")
            inserted = await insert(
                conn,
                "sessions",
                encode(session, SESSION_JSON),
                " ON CONFLICT(session_id) DO NOTHING RETURNING session_id",
            )
            if inserted is None:
                existing = await self.get_session(session.owner_id, session.session_id, tx)
                raise AppError(
                    "stale_resource" if existing else "session_not_found",
                    "Session identity already exists",
                )
        else:
            old = await self.get_session(session.owner_id, session.session_id, tx, for_update=True)
            if old is None:
                raise AppError("session_not_found", "Session not found")
            if old.revision != expected_revision:
                raise AppError("stale_resource", "Session revision changed")
            if old.run_id is not None or old.status not in {"ask", "confirm"}:
                raise AppError("invalid_session_state", "Session is no longer clarifiable")
            if session.brief_version != old.brief_version + 1:
                raise AppError("stale_brief", "Processed clarification must advance brief version")
            self._immutable_session(
                old,
                session,
                {
                    "status",
                    "revision",
                    "brief_draft",
                    "brief_version",
                    "pending_questions",
                    "missing_fields",
                    "clarification_round",
                    "clarification_limit_reached",
                    "source_selection",
                    "failure",
                    "updated_at",
                },
            )
            data = encode(session, SESSION_JSON)
            columns = [key for key in data if key not in {"session_id", "owner_id"}]
            assignments = ",".join(f"{key}=${i + 1}" for i, key in enumerate(columns))
            values = [data[key] for key in columns]
            row = await conn.fetchrow(
                f"UPDATE sessions SET {assignments} WHERE session_id=${len(values) + 1} "
                f"AND owner_id=${len(values) + 2} AND revision=${len(values) + 3} RETURNING session_id",
                *values,
                session.session_id,
                session.owner_id,
                expected_revision,
            )
            if row is None:
                raise AppError("stale_resource", "Session revision changed")
        await insert(conn, "briefs", encode(change.brief, BRIEF_JSON))
        await self._append_messages(session.session_id, change.messages, conn)

    async def list_messages(self, owner: UUID, session_id: UUID, tx=None) -> list[Message]:
        async with self.store.read(tx) as conn:
            rows = await conn.fetch(
                "SELECT m.* FROM messages m JOIN sessions s USING(session_id) "
                "WHERE s.owner_id=$1 AND s.session_id=$2 ORDER BY m.sequence",
                owner,
                session_id,
            )
            return [decode(Message, row, {"assessment"}) for row in rows]

    async def load_brief(
        self, owner: UUID, session_id: UUID, version: int, tx=None
    ) -> BriefRecord | None:
        async with self.store.read(tx) as conn:
            row = await conn.fetchrow(
                "SELECT b.* FROM briefs b JOIN sessions s USING(session_id) "
                "WHERE s.owner_id=$1 AND b.session_id=$2 AND b.version=$3",
                owner,
                session_id,
                version,
            )
            return decode(BriefRecord, row, BRIEF_JSON)

    async def freeze_and_create_run(self, commit: FreezeCommit, tx) -> ResearchRun:
        commit = FreezeCommit.model_validate(commit)
        conn = self.store.connection(tx)
        session, brief = commit.session, commit.brief
        old = await self.get_session(session.owner_id, session.session_id, tx, for_update=True)
        if old is None:
            raise AppError("session_not_found", "Session not found")
        if old.revision != commit.expected_revision:
            raise AppError("stale_resource", "Session revision changed")
        if old.brief_version != brief.version:
            raise AppError("stale_brief", "Brief version changed")
        if old.status != "confirm" or old.run_id is not None:
            raise AppError("invalid_session_state", "Session is not awaiting confirmation")
        self._immutable_session(
            old, session, {"status", "revision", "run_id", "failure", "updated_at"}
        )
        row = await conn.fetchrow(
            "UPDATE briefs SET frozen_at=$3,confirmed_by=$4,content_hash=$5 "
            "WHERE session_id=$1 AND version=$2 AND frozen_at IS NULL AND content=$6::jsonb "
            "AND source_selection=$7::jsonb RETURNING session_id",
            session.session_id,
            brief.version,
            brief.frozen_at,
            brief.confirmed_by,
            brief.content_hash,
            brief.content.model_dump_json(),
            brief.source_selection.model_dump_json(),
        )
        if row is None:
            raise AppError("stale_brief", "Frozen candidate does not match current brief")
        await insert(conn, "research_runs", encode(commit.run, RUN_JSON))
        snapshot = encode(commit.checkpoint, {"state"}) | {"session_id": session.session_id}
        await insert(conn, "phase_snapshots", snapshot)
        await self._append_messages(session.session_id, commit.messages, conn)
        row = await conn.fetchrow(
            "UPDATE sessions SET status='ready',revision=$3,run_id=$4,failure=NULL,updated_at=$5 "
            "WHERE session_id=$1 AND owner_id=$2 AND revision=$6 RETURNING session_id",
            session.session_id,
            session.owner_id,
            session.revision,
            session.run_id,
            session.updated_at,
            commit.expected_revision,
        )
        if row is None:
            raise AppError("stale_resource", "Session revision changed")
        return commit.run

    async def get_run(self, owner: UUID, run_id: UUID, tx=None) -> ResearchRun | None:
        async with self.store.read(tx) as conn:
            row = await conn.fetchrow(
                "SELECT r.* FROM research_runs r JOIN sessions s USING(session_id) "
                "WHERE s.owner_id=$1 AND r.run_id=$2",
                owner,
                run_id,
            )
            return decode(ResearchRun, row, RUN_JSON)

    async def load_checkpoint(
        self, owner: UUID, run_id: UUID, seq: int, tx=None
    ) -> Checkpoint | None:
        async with self.store.read(tx) as conn:
            row = await conn.fetchrow(
                "SELECT p.* FROM phase_snapshots p JOIN sessions s USING(session_id) "
                "WHERE s.owner_id=$1 AND p.run_id=$2 AND p.seq=$3",
                owner,
                run_id,
                seq,
            )
            return decode(Checkpoint, row, {"state"})


class _Requests:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _lease(lease_s):
        if type(lease_s) is not int or lease_s <= 0:
            raise ValueError("Operation lease must be a positive integer")

    async def reserve(self, owner, operation, key, request_hash, tx, *, lease_s=120):
        self._lease(lease_s)
        conn = self.store.connection(tx)
        now = await conn.fetchval("SELECT clock_timestamp()")
        candidate = IdempotencyRecord(
            owner_id=owner,
            operation=operation,
            key=key,
            request_hash=request_hash,
            state="in_progress",
            lease_expires_at=now + timedelta(seconds=lease_s),
            response_status=None,
            response_body=None,
            resource_id=None,
        )
        row = await insert(
            conn,
            "idempotency_requests",
            encode(candidate, {"response_body"}),
            " ON CONFLICT(owner_id,operation,key) DO NOTHING RETURNING *",
        )
        if row is not None:
            return decode(IdempotencyRecord, row, {"response_body"})
        row = await conn.fetchrow(
            "SELECT * FROM idempotency_requests WHERE owner_id=$1 AND operation=$2 AND key=$3 FOR UPDATE",
            candidate.owner_id,
            candidate.operation,
            candidate.key,
        )
        if row is None:
            raise AppError(
                "request_in_progress", "Request reservation changed; retry", retryable=True
            )
        current = decode(IdempotencyRecord, row, {"response_body"})
        if current.request_hash != candidate.request_hash:
            raise AppError("idempotency_conflict", "Request key binds a different body")
        if current.state == "completed":
            return current
        now = await conn.fetchval("SELECT clock_timestamp()")
        if current.lease_expires_at > now:
            raise AppError(
                "request_in_progress",
                "Request is already processing",
                retryable=True,
                details={
                    "retry_after_s": max(
                        1, int((current.lease_expires_at - now).total_seconds()) + 1
                    )
                },
            )
        row = await conn.fetchrow(
            "UPDATE idempotency_requests SET lease_expires_at=$4,updated_at=clock_timestamp() "
            "WHERE owner_id=$1 AND operation=$2 AND key=$3 RETURNING *",
            current.owner_id,
            current.operation,
            current.key,
            now + timedelta(seconds=lease_s),
        )
        return decode(IdempotencyRecord, row, {"response_body"})

    @staticmethod
    def _identity(reservation):
        reservation = IdempotencyRecord.model_validate(reservation)
        return (
            reservation.owner_id,
            reservation.operation,
            reservation.key,
            reservation.request_hash,
            reservation.lease_expires_at,
        )

    async def renew(self, reservation, tx, *, lease_s=120):
        self._lease(lease_s)
        row = await self.store.connection(tx).fetchrow(
            "UPDATE idempotency_requests SET lease_expires_at=clock_timestamp()+make_interval(secs=>$6),updated_at=clock_timestamp() "
            "WHERE owner_id=$1 AND operation=$2 AND key=$3 AND request_hash=$4 AND lease_expires_at=$5 "
            "AND state='in_progress' AND lease_expires_at>clock_timestamp() RETURNING *",
            *self._identity(reservation),
            lease_s,
        )
        if row is None:
            raise AppError(
                "request_in_progress", "Request reservation is no longer owned", retryable=True
            )
        return decode(IdempotencyRecord, row, {"response_body"})

    async def complete(self, reservation, response_status, response_body, tx, *, resource_id=None):
        if type(response_status) is int and response_status >= 500:
            raise ValueError("Transient server failure must release reservation, not complete it")
        completed = IdempotencyRecord.model_validate(
            reservation.model_dump()
            | {
                "state": "completed",
                "response_status": response_status,
                "response_body": response_body,
                "resource_id": resource_id,
            }
        )
        row = await self.store.connection(tx).fetchrow(
            "UPDATE idempotency_requests SET state='completed',response_status=$6,response_body=$7::jsonb,resource_id=$8,updated_at=clock_timestamp() "
            "WHERE owner_id=$1 AND operation=$2 AND key=$3 AND request_hash=$4 AND lease_expires_at=$5 "
            "AND state='in_progress' AND lease_expires_at>clock_timestamp() RETURNING *",
            *self._identity(reservation),
            completed.response_status,
            encode(completed, {"response_body"})["response_body"],
            completed.resource_id,
        )
        if row is None:
            raise AppError(
                "request_in_progress", "Request reservation is no longer owned", retryable=True
            )
        return decode(IdempotencyRecord, row, {"response_body"})

    async def release(self, reservation, tx):
        result = await self.store.connection(tx).fetchrow(
            "DELETE FROM idempotency_requests WHERE owner_id=$1 AND operation=$2 AND key=$3 AND request_hash=$4 "
            "AND lease_expires_at=$5 AND state='in_progress' AND lease_expires_at>clock_timestamp() RETURNING owner_id",
            *self._identity(reservation),
        )
        if result is None:
            raise AppError(
                "request_in_progress", "Request reservation is no longer owned", retryable=True
            )
