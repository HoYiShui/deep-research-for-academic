"""Composition root: wire adapters into the service graph.

The interface layer calls get_container() once at startup to obtain the
assembled services. Each integration slice swaps one fake for its real adapter;
the container accepts overrides so tests can inject fakes/mocks.
"""

from __future__ import annotations

from application.auth_service import AuthService
from application.knowledge_base_service import KnowledgeBaseService
from application.orchestrator import Orchestrator
from application.research_service import ResearchService
from application.session_service import SessionService
from application.settings import Settings
from application.sse import EventBus
from infrastructure.embedding.bge_m3 import BGEM3Embedding
from infrastructure.embedding.bge_reranker import BGEReranker
from infrastructure.llm.deepseek import DeepSeekLLM
from infrastructure.parser.pdf import MinerUParser
from infrastructure.retrieval.local import LocalRetrieval
from infrastructure.sandbox.docker import DockerExecution
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.composite import CompositeSearch
from infrastructure.storage.memory import InMemoryCancel, InMemoryDocumentStore, InMemoryUserStore
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
        users=None,
        knowledge_base=None,
        auth=None,
        bus=None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings.load()
        config = self.settings
        if config.llm_local and llm is None:
            raise ValueError("a local LLM adapter must be configured explicitly")
        if config.dr4a_env == "production" and execution is None:
            raise ValueError("an isolated production execution adapter must be configured")
        self.bus = bus or EventBus()
        self.llm = llm or DeepSeekLLM(
            api_key=config.anthropic_api_key.get_secret_value(),
            base_url=config.anthropic_base_url, model=config.llm_model,
            timeout_s=config.llm_timeout_s,
        )
        self.search = search or CompositeSearch([
            ("arxiv", ArxivSearch()),
            ("bocha", BochaSearch(
                api_key=config.bocha_api_key.get_secret_value(),
                timeout_s=config.search_timeout_s,
            )),
        ])
        self.embedding = embedding or BGEM3Embedding(config.bge_m3_model_path)
        self.vector = vector or MilvusStore(config.milvus_uri)
        self.reranker = reranker or BGEReranker(config.bge_reranker_model_path)
        self.retrieval = retrieval or LocalRetrieval(self.embedding, self.vector, self.reranker)
        self.execution = execution or DockerExecution()
        self.store = store or PostgresStateStore(config.database_url.get_secret_value())
        self.cancel = cancel or InMemoryCancel()
        self.documents = documents or InMemoryDocumentStore()
        self.users = users or InMemoryUserStore()
        self.auth = auth or AuthService(self.users, secret=config.jwt_secret.get_secret_value())
        self.knowledge_base = knowledge_base or KnowledgeBaseService(
            MinerUParser(), self.embedding, self.vector, self.documents
        )
        self.sessions = SessionService(self.llm, self.store)
        self.orchestrator = Orchestrator(
            self.bus, self.cancel, self.store, self.llm, self.search, self.retrieval, self.execution
        )
        self.research = ResearchService(self.sessions, self.orchestrator, self.store, self.cancel)


_container: Container | None = None


def get_container() -> Container:
    """Return the singleton service container."""
    global _container
    if _container is None:
        _container = Container()
    return _container
