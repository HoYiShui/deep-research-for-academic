"""Application ports: abstract interfaces consumed by the orchestrator.

StateStorePort is implemented by PostgreSQL (durable truth + snapshots);
CancellationPort is an in-process dict in V1 (Redis in V2).
"""
from __future__ import annotations

from typing import Protocol


class StateStorePort(Protocol):
    """Persist business truth and phase-level snapshots."""

    async def save_session(self, session_id: str, state: dict) -> None: ...

    async def load_session(self, session_id: str) -> dict | None: ...

    async def save_snapshot(self, session_id: str, phase: str, state: dict) -> None: ...

    async def load_latest_snapshot(self, session_id: str, phase: str) -> dict | None: ...


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
