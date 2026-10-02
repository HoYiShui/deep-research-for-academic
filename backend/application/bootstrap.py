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
from infrastructure.storage.memory import InMemoryCancel


class Container:
    """Assembled service graph (fakes for the walking skeleton)."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.llm = FakeLLM()
        self.search = FakeSearch()
        self.store = FakeStateStore()
        self.cancel = InMemoryCancel()
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
