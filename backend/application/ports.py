"""Application ports: abstract interfaces consumed by the orchestrator.

StateStorePort is implemented by PostgreSQL (durable truth + snapshots);
CancellationPort is an in-process dict in V1 (Redis in V2).
"""
from __future__ import annotations

from typing import Protocol


class StateStorePort(Protocol):
    """Persist the durable truth: sessions, clarify history, briefs, reports, snapshots.

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
