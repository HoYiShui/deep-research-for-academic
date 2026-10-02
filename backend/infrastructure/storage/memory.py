"""In-memory cancellation and document-progress stores (V1 single-process)."""

from __future__ import annotations


class InMemoryCancel:
    """CancellationPort implementation backed by a process-local dict."""

    def __init__(self) -> None:
        self._flags: dict[str, bool] = {}

    def is_cancelled(self, session_id: str) -> bool:
        return self._flags.get(session_id, False)

    def set_cancelled(self, session_id: str) -> None:
        self._flags[session_id] = True


class InMemoryDocumentStore:
    """DocumentStorePort implementation backed by a process-local dict."""

    def __init__(self) -> None:
        self._documents: dict[str, dict] = {}

    async def save(self, document_id: str, state: dict) -> None:
        self._documents[document_id] = state

    async def load(self, document_id: str) -> dict | None:
        return self._documents.get(document_id)

    async def list(self) -> list[dict]:
        return [{"document_id": k, **v} for k, v in self._documents.items()]

    async def delete(self, document_id: str) -> None:
        self._documents.pop(document_id, None)
