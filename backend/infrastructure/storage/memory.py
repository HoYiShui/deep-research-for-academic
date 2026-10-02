"""In-memory cancellation flag (V1 single-process)."""

from __future__ import annotations


class InMemoryCancel:
    """CancellationPort implementation backed by a process-local dict."""

    def __init__(self) -> None:
        self._flags: dict[str, bool] = {}

    def is_cancelled(self, session_id: str) -> bool:
        return self._flags.get(session_id, False)

    def set_cancelled(self, session_id: str) -> None:
        self._flags[session_id] = True
