"""Composition root: wire fake adapters into the service graph.

The interface layer calls get_container() once at startup to obtain the
assembled services. Real adapters replace the fakes in later slices.
"""

from __future__ import annotations

from application.orchestrator import Orchestrator
from application.research_service import ResearchService
from application.session_service import SessionService
from application.sse import EventBus
from infrastructure.fake import FakeLLM, FakeSearch, FakeStateStore


class _InMemoryCancel:
    """CancellationPort V1: in-process dict (moves to infrastructure later)."""

    def __init__(self) -> None:
        self._flags: dict[str, bool] = {}

    def is_cancelled(self, session_id: str) -> bool:
        return self._flags.get(session_id, False)

    def set_cancelled(self, session_id: str) -> None:
        self._flags[session_id] = True


class Container:
    """Assembled service graph (fakes for the walking skeleton)."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.llm = FakeLLM()
        self.search = FakeSearch()
        self.store = FakeStateStore()
        self.cancel = _InMemoryCancel()
        self.sessions = SessionService(self.llm, self.store)
        self.orchestrator = Orchestrator(self.bus, self.cancel)
        self.research = ResearchService(self.sessions, self.orchestrator)


_container: Container | None = None


def get_container() -> Container:
    """Return the singleton service container."""
    global _container
    if _container is None:
        _container = Container()
    return _container
