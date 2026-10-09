"""Composition root: wire adapters into the service graph.

The interface layer calls get_container() once at startup to obtain the
assembled services. Each integration slice swaps one fake for its real adapter;
the container accepts overrides so tests can inject fakes/mocks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg

from application.auth_service import AuthService
from application.document_ingestion import DocumentIngestionService
from application.errors import AppError
from application.identity import ensure_development_identity
from application.knowledge_base_management import KnowledgeBaseManagementService
from application.knowledge_base_service import KnowledgeBaseService
from application.knowledge_cleanup import KnowledgeCleanupService
from application.orchestrator import Orchestrator
from application.research_artifacts import ResearchArtifacts
from application.research_queries import ResearchQueries
from application.research_service import LegacyResearchService, ResearchService
from application.run_sse import RunEventBus, RunEventStream
from application.session_service import LegacySessionService, SessionService
from application.settings import Settings
from application.sse import EventBus
from application.task_runner import TaskRunner
from application.web_search import web_search_binding
from infrastructure.clock import SystemClock
from infrastructure.embedding.bge_m3 import BGEM3Embedding
from infrastructure.embedding.bge_reranker import BGEReranker
from infrastructure.llm.deepseek import DeepSeekLLM
from infrastructure.parser.pdf import MinerUParser
from infrastructure.retrieval.local import LocalRetrieval
from infrastructure.sandbox.docker import DockerExecution
from infrastructure.storage.artifacts import MinioArtifactStore
from infrastructure.storage.content import MinioContentStore
from infrastructure.storage.memory import InMemoryCancel, InMemoryDocumentStore, InMemoryUserStore
from infrastructure.storage.migrations import run_migrations
from infrastructure.storage.postgres import PostgresStateStore
from infrastructure.storage.research_postgres import PostgresResearchStore
from infrastructure.vector.milvus import MilvusIndex, MilvusStore


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
        research_store=None,
    ) -> None:
        self.settings = settings or Settings.load()
        self.research_queries = (
            ResearchQueries(research_store, research_store.research)
            if research_store is not None
            else None
        )
        config = self.settings
        if config.llm_local and llm is None:
            raise ValueError("a local LLM adapter must be configured explicitly")
        if config.dr4a_env == "production" and execution is None:
            raise ValueError("an isolated production execution adapter must be configured")
        self.bus = bus or EventBus()
        self.llm = llm or DeepSeekLLM(
            api_key=config.anthropic_api_key.get_secret_value(),
            base_url=config.anthropic_base_url,
            model=config.llm_model,
            timeout_s=config.llm_timeout_s,
        )
        self.search = search or web_search_binding(config).adapter
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
        self.sessions = LegacySessionService(self.llm, self.store)
        self.orchestrator = Orchestrator(
            self.bus, self.cancel, self.store, self.llm, self.search, self.retrieval, self.execution
        )
        self.research = LegacyResearchService(
            self.sessions, self.orchestrator, self.store, self.cancel
        )

    async def aclose(self) -> None:
        """Release container-owned adapters even when one adapter fails to close."""
        errors = []
        seen = set()
        for adapter in (self.store, self.llm, self.search, self.vector, self.execution):
            if id(adapter) in seen:
                continue
            seen.add(id(adapter))
            close = getattr(adapter, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:  # noqa: BLE001 -- finish cleanup, then re-raise all failures
                    errors.append(exc)
        if errors:
            raise ExceptionGroup("Adapter shutdown failed", errors)


class _PendingAuth:
    def verify_token(self, token):
        # Fail closed until the persistent JWT/auth implementation in T053.
        return None

    async def register(self, *args):
        raise AppError("service_not_ready", "Persistent authentication is not ready")

    async def login(self, *args):
        raise AppError("service_not_ready", "Persistent authentication is not ready")


class HttpRuntime:
    """Mono HTTP composition; never wires a legacy writable state store."""

    def __init__(
        self,
        *,
        settings: Settings,
        research_store=None,
        llm=None,
        backup_dir=None,
        run_executor_factory=None,
    ):
        self.settings, self.repository_store = settings, research_store
        if run_executor_factory is not None and not callable(run_executor_factory):
            raise TypeError("Run executor factory must be explicitly callable")
        self.run_executor_factory = run_executor_factory
        self.runner = None
        self.debug_execution = None
        self.pool = None
        self.artifact_store = MinioArtifactStore(
            settings.minio_endpoint,
            settings.minio_access_key.get_secret_value(),
            settings.minio_secret_key.get_secret_value(),
            settings.minio_bucket,
            secure=settings.minio_secure,
        )
        self.knowledge_content = MinioContentStore(
            settings.minio_endpoint,
            settings.minio_access_key.get_secret_value(),
            settings.minio_secret_key.get_secret_value(),
            settings.minio_bucket,
            secure=settings.minio_secure,
        )
        self.knowledge_index = MilvusIndex(settings.milvus_uri, timeout_s=settings.vector_timeout_s)
        self.backup_dir = (
            Path(backup_dir)
            if backup_dir is not None
            else Path(__file__).resolve().parents[1] / ".local" / "legacy-backups"
        )
        if settings.llm_local and llm is None:
            raise AppError(
                "service_not_ready", "A local model adapter must be explicitly configured"
            )
        self.llm = (
            llm
            if llm is not None
            else DeepSeekLLM(
                retries=0,
                api_key=settings.anthropic_api_key.get_secret_value(),
                base_url=settings.anthropic_base_url,
                model=settings.llm_model,
                timeout_s=settings.llm_timeout_s,
                max_tokens=48000,
            )
        )
        self.clock = SystemClock()
        self.auth = _PendingAuth()

    async def prepare(self, *, start_runner: bool = True):
        if type(start_runner) is not bool:
            raise TypeError("Runner startup mode must be an explicit bool")
        if self.repository_store is None:
            self.pool = await asyncpg.create_pool(
                self.settings.database_url.get_secret_value(), min_size=1, max_size=10
            )
            # Nonempty legacy data must be backed up before the transactional cutover.
            await run_migrations(self.pool, backup_dir=self.backup_dir)
            self.repository_store = PostgresResearchStore(self.pool)
        store = self.repository_store
        if not self.settings.dr4a_auth_required:
            await ensure_development_identity(store, store.users, self.clock)
        self.sessions = SessionService(
            self.llm,
            self.clock,
            max_rounds=self.settings.clarify_rounds,
            timeout_s=self.settings.llm_timeout_s,
        )
        self.research = ResearchService(
            uow=store,
            research=store.research,
            requests=store.requests,
            users=store.users,
            sessions=self.sessions,
            clock=self.clock,
            settings=self.settings,
        )
        self.research_queries = ResearchQueries(store, store.research)
        self.research_artifacts = ResearchArtifacts(self.research_queries, self.artifact_store)
        self.ingestion = DocumentIngestionService(
            store, store.knowledge, store.requests, self.knowledge_content
        )
        self.knowledge_cleanup = KnowledgeCleanupService(
            store,
            store.knowledge,
            self.knowledge_index,
            self.knowledge_content,
            self.clock,
            lease_s=self.settings.lease_s,
            heartbeat_s=self.settings.heartbeat_s,
        )
        self.knowledge_management = KnowledgeBaseManagementService(
            store,
            store.knowledge,
            store.requests,
            self.knowledge_index,
            self.clock,
            index_version=self.settings.index_version,
            ingestion=self.ingestion,
            cleanup=self.knowledge_cleanup,
            lease_s=self.settings.lease_s,
            heartbeat_s=self.settings.heartbeat_s,
        )
        self.run_event_bus = RunEventBus(queue_size=self.settings.sse_queue_size)
        self.run_events = RunEventStream(
            self.research_queries,
            self.run_event_bus,
            poll_s=self.settings.scan_s,
            heartbeat_s=self.settings.sse_heartbeat_s,
        )
        # A CLI composes its own owner/Run-scoped runner. Preparing shared
        # services must not also scan or execute unrelated HTTP Runs, even if
        # the caller inherits the development HTTP execution setting.
        if not start_runner:
            return
        # Always maintain durable cancellation/expired leases. Claiming paid
        # work remains disabled unless an executor is explicitly composed.
        if self.run_executor_factory is None and self.settings.dr4a_debug_runner:
            from application.debug_runtime import DebugExecution

            self.debug_execution = DebugExecution(self)
            self.run_executor_factory = lambda runtime: self.debug_execution.execute

        async def knowledge_maintenance():
            await self.knowledge_management.recover_creating()
            await self.knowledge_cleanup.tick()

        self.runner = TaskRunner(
            store=store,
            execute=self.run_executor_factory(self) if self.run_executor_factory else None,
            settings=self.settings,
            claim_ready=self.run_executor_factory is not None,
            maintenance=knowledge_maintenance,
        )
        self.research.wake = self.runner.wake
        await self.runner.start()
        self.knowledge_cleanup.wake = self.runner.wake
        self.knowledge_cleanup.available = True

    @property
    def knowledge_base(self):
        raise AppError("service_not_ready", "Knowledge base persistence is not ready")

    @property
    def retrieval(self):
        raise AppError("service_not_ready", "Knowledge retrieval is not ready")

    async def aclose(self):
        if hasattr(self, "knowledge_cleanup"):
            self.knowledge_cleanup.available = False
        try:
            try:
                # Keep the model and PG available until held work is stopped
                # and its interruption has been recorded under the lease.
                if self.runner is not None:
                    await self.runner.aclose()
            finally:
                try:
                    if self.debug_execution is not None:
                        await self.debug_execution.aclose()
                finally:
                    close = getattr(self.llm, "aclose", None)
                    if close is not None:
                        await close()
        finally:
            try:
                try:
                    await self.artifact_store.close()
                finally:
                    try:
                        await self.knowledge_content.close()
                    finally:
                        await self.knowledge_index.close()
            finally:
                if self.pool is not None:
                    pool, self.pool = self.pool, None
                    try:
                        await asyncio.wait_for(pool.close(), timeout=5)
                    except TimeoutError:
                        pool.terminate()


_container: Container | None = None


def get_container(request=None) -> Container:
    """HTTP uses lifespan-owned state; legacy CLI still owns a local singleton."""
    if request is not None:
        container = getattr(request.app.state, "container", None)
        if container is None:
            raise AppError("service_not_ready", "Application has not started", retryable=True)
        return container
    global _container
    if _container is None:
        _container = Container()
    return _container
