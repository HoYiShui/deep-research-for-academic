"""Composition root: wire adapters into the service graph.

The interface layer calls get_container() once at startup to obtain the
assembled services. Each integration slice swaps one fake for its real adapter;
the container accepts overrides so tests can inject fakes/mocks.
"""

from __future__ import annotations

from application.knowledge_base_service import KnowledgeBaseService
from application.orchestrator import Orchestrator
from application.research_service import ResearchService
from application.session_service import SessionService
from application.sse import EventBus
from infrastructure.embedding.bge_m3 import BGEM3Embedding
from infrastructure.embedding.bge_reranker import BGEReranker
from infrastructure.fake import FakeExecution
from infrastructure.llm.deepseek import DeepSeekLLM
from infrastructure.parser.pdf import MinerUParser
from infrastructure.retrieval.local import LocalRetrieval
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.composite import CompositeSearch
from infrastructure.storage.memory import InMemoryCancel, InMemoryDocumentStore
from infrastructure.storage.postgres import PostgresStateStore
from infrastructure.vector.milvus import MilvusStore


class Container:
    """Assembled service graph (real LLM + search + persistence + KB)."""

    def __init__(
        self,
        *,
        llm=None,
        search=None,
        retrieval=None,
        embedding=None,
        vector=None,
        reranker=None,
        execution=None,
        store=None,
        cancel=None,
        documents=None,
        knowledge_base=None,
        bus=None,
    ) -> None:
        self.bus = bus or EventBus()
        self.llm = llm or DeepSeekLLM()
        self.search = search or CompositeSearch([("arxiv", ArxivSearch()), ("bocha", BochaSearch())])
        self.embedding = embedding or BGEM3Embedding()
        self.vector = vector or MilvusStore()
        self.reranker = reranker or BGEReranker()
        self.retrieval = retrieval or LocalRetrieval(self.embedding, self.vector, self.reranker)
        self.execution = execution or FakeExecution()
        self.store = store or PostgresStateStore()
        self.cancel = cancel or InMemoryCancel()
        self.documents = documents or InMemoryDocumentStore()
        self.knowledge_base = knowledge_base or KnowledgeBaseService(
            MinerUParser(), self.embedding, self.vector, self.documents
        )
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
