"""Composition root: wire adapters into the service graph.

The interface layer calls get_container() once at startup to obtain the
assembled services. Each integration slice swaps one fake for its real adapter;
the container accepts overrides so tests can inject fakes/mocks.
"""

from __future__ import annotations

from application.orchestrator import Orchestrator
from application.research_service import ResearchService
from application.session_service import SessionService
from application.sse import EventBus
from infrastructure.fake import FakeExecution, FakeRetrieval, FakeStateStore
from infrastructure.llm.deepseek import DeepSeekLLM
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.composite import CompositeSearch
from infrastructure.storage.memory import InMemoryCancel


class Container:
    """Assembled service graph (real LLM + real search; fakes elsewhere)."""

    def __init__(
        self,
        *,
        llm=None,
        search=None,
        retrieval=None,
        execution=None,
        store=None,
        cancel=None,
        bus=None,
    ) -> None:
        self.bus = bus or EventBus()
        self.llm = llm or DeepSeekLLM()
        self.search = search or CompositeSearch([("arxiv", ArxivSearch()), ("bocha", BochaSearch())])
        self.retrieval = retrieval or FakeRetrieval()
        self.execution = execution or FakeExecution()
        self.store = store or FakeStateStore()
        self.cancel = cancel or InMemoryCancel()
        self.sessions = SessionService(self.llm, self.store)
        self.orchestrator = Orchestrator(
            self.bus, self.cancel, self.store, self.llm, self.search, self.retrieval, self.execution
        )
        self.research = ResearchService(self.sessions, self.orchestrator)


_container: Container | None = None


def get_container() -> Container:
    """Return the singleton service container."""
    global _container
    if _container is None:
        _container = Container()
    return _container
