"""Typed mono-v1 service and atomic repository contracts.

The bottom section retains pre-mono ports for unmigrated callers; those do not
describe target cancellation or durable ownership semantics.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from application.records import (
    ClaimedRun,
    DevelopmentUser,
    FreezeCommit,
    IdempotencyRecord,
    SessionChange,
    SessionInput,
    User,
    ValidatedFrozenInput,
)
from application.tool_budget import ToolBudgetRequest
from application.tool_records import ToolBudgetView, ToolReservation
from domain.content import ContentRef
from domain.documents import ParsedDocument, ParserConfig
from domain.research.facts import FinalReport
from domain.research.models import (
    BriefRecord,
    Failure,
    Message,
    PartialResearchBrief,
    ResearchRun,
    RunConfig,
    SessionState,
    SourceSelection,
)
from domain.research.search import SearchBatch, SearchResult
from domain.research.state import Checkpoint
from domain.research.tool_calls import ToolCallIdentity


class ContentStorePort(Protocol):
    """Shared document/research blobs; ownership is checked by the calling Service."""

    async def put(
        self,
        key: str,
        stream: AsyncIterable[bytes] | bytes,
        *,
        media_type: str,
        expected_hash: str | None = None,
    ) -> ContentRef: ...

    async def get(self, key: str) -> AsyncIterable[bytes]: ...

    async def head(self, key: str) -> ContentRef: ...

    async def delete(self, key: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> None: ...


class DocumentParserPort(Protocol):
    async def parse(self, reference: ContentRef, config: ParserConfig) -> ParsedDocument: ...


SearchOperation = Callable[[], Awaitable[list[SearchResult]]]
SearchAttemptInvoker = Callable[[str, str, int, SearchOperation], Awaitable[list[SearchResult]]]


class ResearchSearchPort(Protocol):
    async def search_batch(
        self,
        query: str,
        *,
        categories: frozenset[str],
        invoke: SearchAttemptInvoker,
        retry: bool = True,
    ) -> SearchBatch: ...


class TransactionPort(Protocol):
    """Opaque adapter-owned transaction; application never touches its connection."""

    @property
    def transaction_id(self) -> UUID: ...


class UnitOfWorkPort(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[TransactionPort]: ...


class UserRepositoryPort(Protocol):
    async def ensure_development(
        self, user: DevelopmentUser, tx: TransactionPort
    ) -> DevelopmentUser: ...

    async def create(self, user: User, tx: TransactionPort) -> None: ...

    async def get_by_id(self, user_id: UUID, tx: TransactionPort | None = None) -> User | None: ...

    async def get_by_email(self, email: str, tx: TransactionPort | None = None) -> User | None: ...


class ResearchRepositoryPort(Protocol):
    """Stage-zero operations; run lease/termination extensions arrive in T015."""

    async def get_session(
        self,
        owner: UUID,
        session_id: UUID,
        tx: TransactionPort | None = None,
        *,
        for_update: bool = False,
    ) -> SessionState | None: ...

    async def commit_session_change(
        self, expected_revision: int, change: SessionChange, tx: TransactionPort
    ) -> None: ...

    async def list_messages(
        self, owner: UUID, session_id: UUID, tx: TransactionPort | None = None
    ) -> list[Message]: ...

    async def load_brief(
        self, owner: UUID, session_id: UUID, version: int, tx: TransactionPort | None = None
    ) -> BriefRecord | None: ...

    async def freeze_and_create_run(
        self, commit: FreezeCommit, tx: TransactionPort, *, queue_limit: int = 20
    ) -> ResearchRun: ...

    async def get_run(
        self, owner: UUID, run_id: UUID, tx: TransactionPort | None = None
    ) -> ResearchRun | None: ...

    async def load_checkpoint(
        self, owner: UUID, run_id: UUID, seq: int, tx: TransactionPort | None = None
    ) -> Checkpoint | None: ...

    async def claim_run(
        self,
        worker: str,
        tx: TransactionPort,
        *,
        owner: UUID | None = None,
        run_id: UUID | None = None,
        lease_s: int = 90,
        global_limit: int = 2,
        owner_limit: int = 1,
    ) -> ClaimedRun | None: ...

    async def renew_lease(
        self, claimed: ClaimedRun, tx: TransactionPort, *, lease_s: int = 90
    ) -> ClaimedRun: ...

    async def check_run_lease(self, claimed: ClaimedRun, tx: TransactionPort) -> ResearchRun: ...

    async def load_latest_checkpoint(
        self, owner: UUID, run_id: UUID, tx: TransactionPort | None = None
    ) -> Checkpoint | None: ...

    async def commit_checkpoint(
        self, claimed: ClaimedRun, expected_seq: int, checkpoint: Checkpoint, tx: TransactionPort
    ) -> ClaimedRun: ...

    async def request_cancel(
        self, owner: UUID, session_id: UUID, tx: TransactionPort
    ) -> SessionState: ...

    async def finish_cancelled(self, claimed: ClaimedRun, tx: TransactionPort) -> ResearchRun: ...

    async def fail_run(
        self, claimed: ClaimedRun, failure: Failure, tx: TransactionPort
    ) -> ResearchRun: ...

    async def resume_run(
        self,
        owner: UUID,
        session_id: UUID,
        seq: int,
        config: RunConfig,
        tx: TransactionPort,
        *,
        queue_limit: int = 20,
    ) -> ResearchRun: ...

    async def scan_interrupted(
        self,
        tx: TransactionPort,
        *,
        queue_timeout_s: int = 1800,
        limit: int = 100,
        owner: UUID | None = None,
        run_id: UUID | None = None,
    ) -> list[ResearchRun]: ...

    async def publish_report(
        self, claimed: ClaimedRun, expected_seq: int, checkpoint: Checkpoint, tx: TransactionPort
    ) -> ResearchRun: ...

    async def load_report(
        self, owner: UUID, run_id: UUID, tx: TransactionPort | None = None
    ) -> FinalReport | None: ...


class ToolCallRepositoryPort(Protocol):
    """Separate coordinator capability; transactions share the parent Run lock."""

    async def reserve_tool_call(
        self,
        claimed: ClaimedRun,
        identity: ToolCallIdentity,
        request: ToolBudgetRequest,
        tx: TransactionPort,
        *,
        elapsed_s: float = 0,
        allow_uncertain_replay: bool = False,
        skip_result_recovery: bool = False,
    ) -> ToolReservation: ...

    async def stage_tool_result(
        self,
        claimed: ClaimedRun,
        reservation: ToolReservation,
        reference: ContentRef,
        tokens_used: int,
        tx: TransactionPort,
    ) -> ToolReservation: ...

    async def finish_tool_call(
        self,
        claimed: ClaimedRun,
        reservation: ToolReservation,
        tx: TransactionPort,
        *,
        reference: ContentRef | None = None,
        tokens_used: int | None = None,
        failure: Failure | None = None,
    ) -> ToolReservation: ...

    async def load_tool_budget(
        self,
        owner: UUID,
        run_id: UUID,
        tx: TransactionPort | None = None,
    ) -> ToolBudgetView | None: ...


class RequestStorePort(Protocol):
    async def reserve(
        self,
        owner: UUID,
        operation: str,
        key: str,
        request_hash: str,
        tx: TransactionPort,
        *,
        lease_s: int = 120,
    ) -> IdempotencyRecord: ...

    async def renew(
        self, reservation: IdempotencyRecord, tx: TransactionPort, *, lease_s: int = 120
    ) -> IdempotencyRecord: ...

    async def complete(
        self,
        reservation: IdempotencyRecord,
        response_status: int,
        response_body: dict | None,
        tx: TransactionPort,
        *,
        resource_id: UUID | None = None,
    ) -> IdempotencyRecord: ...

    async def release(self, reservation: IdempotencyRecord, tx: TransactionPort) -> None: ...


class SessionServicePort(Protocol):
    async def assess_initial(self, value: SessionInput) -> SessionChange: ...

    async def assess_round(
        self,
        value: SessionInput,
        answer: str,
        *,
        brief_patch: PartialResearchBrief | None = None,
        source_selection: SourceSelection | None = None,
    ) -> SessionChange: ...

    async def assess_rejection(
        self, value: SessionInput, feedback: str, *, source_selection: SourceSelection | None = None
    ) -> SessionChange: ...

    async def validate_confirmation(
        self, session: SessionState, expected_brief_version: int
    ) -> ValidatedFrozenInput: ...


class StateStorePort(Protocol):
    """Legacy port, removed as ResearchService/Orchestrator migrate in T011/T017.

    Mirrors the seven stable tables (users is UserStorePort; audit_log is V2).
    Messages are append-only (audit); briefs/reports are versioned.
    """

    async def create_session(self, session_id: str, status: str = "clarify") -> None: ...

    async def set_session_status(self, session_id: str, status: str) -> None: ...

    async def get_session_status(self, session_id: str) -> str | None: ...

    async def append_message(self, session_id: str, role: str, content: str) -> None: ...

    async def list_messages(self, session_id: str) -> list[dict]: ...

    async def save_brief(self, session_id: str, brief: dict, task_type: str = "") -> None: ...

    async def load_brief(self, session_id: str) -> dict | None: ...

    async def save_snapshot(self, session_id: str, phase: str, state: dict) -> None: ...

    async def load_latest_snapshot(self, session_id: str, phase: str) -> dict | None: ...

    async def save_report(self, session_id: str, content: dict) -> None: ...

    async def load_report(self, session_id: str) -> dict | None: ...


class CancellationPort(Protocol):
    """Check and set a cancellation flag (cross-request; in-process dict in V1)."""

    def is_cancelled(self, session_id: str) -> bool: ...

    def set_cancelled(self, session_id: str) -> None: ...


class DocumentStorePort(Protocol):
    """Persist knowledge-base document progress (status: processing/done/failed)."""

    async def save(self, document_id: str, state: dict) -> None: ...

    async def load(self, document_id: str) -> dict | None: ...

    async def list(self) -> list[dict]: ...

    async def delete(self, document_id: str) -> None: ...


class UserStorePort(Protocol):
    """Persist users (email -> password hash)."""

    async def create(self, user_id: str, email: str, password_hash: str) -> None: ...

    async def get_by_email(self, email: str) -> dict | None: ...
