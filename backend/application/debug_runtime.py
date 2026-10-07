"""Shared public-source execution composition, with opt-in development HTTP use."""

import asyncio
from pathlib import Path

from application.errors import AppError
from application.fetch_tools import FetchBinding
from application.phase_executor import PhaseExecutor
from application.phase_tools import ModelBinding
from application.phase_workers import plan_worker, research_worker
from application.report_serializer import ReportPublisher
from application.run_driver import RunDriver
from application.run_sse import RunEventStream
from application.search_tools import SearchBinding, SearchProvider
from infrastructure.fetch.document import HTTPDocumentFetch
from infrastructure.parser.html import HTML_PARSER_VERSION, HTMLDocumentParser
from infrastructure.parser.mineru_output import MINERU_PARSER_VERSION
from infrastructure.parser.pdf import MinerUDocumentParser
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.composite import CompositeSearch
from infrastructure.storage.content import MinioContentStore
from infrastructure.storage.content_cache import MinioResultCache


class PublicResearchExecution:
    def __init__(self, runtime, *, committed=None, diagnostic=None):
        config = runtime.settings
        if config.llm_local:
            raise AppError(
                "service_not_ready", "Public executor requires a configured remote model"
            )
        version = config.parser_version
        if version not in {HTML_PARSER_VERSION, MINERU_PARSER_VERSION}:
            raise AppError("service_not_ready", "Explicit HTML/PDF parser version is required")
        if (
            version == MINERU_PARSER_VERSION
            and not (Path(config.mineru_models_dir) / "dr4a-models.json").is_file()
        ):
            raise AppError("service_not_ready", "Prepared MINERU_MODELS_DIR is required")
        options = {
            "endpoint": config.minio_endpoint,
            "access_key": config.minio_access_key.get_secret_value(),
            "secret_key": config.minio_secret_key.get_secret_value(),
            "bucket": config.minio_bucket,
            "secure": config.minio_secure,
        }
        self.cache, self.content = MinioResultCache(**options), MinioContentStore(**options)
        self.search = CompositeSearch(
            [
                ("arxiv", ArxivSearch(timeout_s=config.search_timeout_s)),
                (
                    "bocha",
                    BochaSearch(
                        config.bocha_api_key.get_secret_value(), timeout_s=config.search_timeout_s
                    ),
                ),
            ],
            timeout_s=config.search_timeout_s,
        )
        self.parser = (
            HTMLDocumentParser(self.content)
            if version == HTML_PARSER_VERSION
            else MinerUDocumentParser(
                self.content, config.mineru_models_dir, timeout_s=config.parser_timeout_s
            )
        )

        def fetch(run_id, parser_config):
            if parser_config.parser_version != version:
                raise AppError("config_unavailable", "Frozen parser differs from debug runtime")
            return HTTPDocumentFetch(self.content, self.parser, parser_config, run_id)

        def projected(value):
            point, run = value.checkpoint, value.claimed.run
            runtime.run_event_bus.emit(
                RunEventStream._projection(
                    {
                        "session_id": str(run.session_id),
                        "run_id": str(run.run_id),
                        "checkpoint_seq": point.seq,
                        "phase": point.phase,
                        "status": run.status,
                    }
                )
            )

        # Missing analyze/write/review fail at their actual stage, preserving the
        # last checkpoint for CLI. Never add fake stages to make the TUI green.
        self.driver = RunDriver(
            store=runtime.repository_store,
            cache=self.cache,
            executor=PhaseExecutor({"plan": plan_worker, "research": research_worker}),
            model=ModelBinding(
                runtime.llm,
                "anthropic_compatible",
                config.llm_model,
                config.llm_revision,
                config.run_tokens - config.run_terminal_reserved_tokens,
                output_token_limit=16384,
            ),
            model_slots=asyncio.Semaphore(config.llm_concurrency),
            clock=runtime.clock,
            publish=ReportPublisher(runtime.repository_store, runtime.clock).publish,
            unit_committed=committed if committed is not None else projected,
            phase_committed=committed if committed is not None else projected,
            finished=lambda run: None,
            diagnostic=diagnostic if diagnostic is not None else runtime.run_event_bus.emit,
            search=SearchBinding(
                self.search,
                (
                    SearchProvider(name="arxiv", category="papers", revision="arxiv-id-lookup-v2"),
                    SearchProvider(name="bocha", category="web", revision="bocha-v1"),
                ),
            ),
            fetch=FetchBinding(fetch),
        )

    async def execute(self, claimed, stop):
        await self.driver.execute(claimed, stop)

    async def aclose(self):
        try:
            await self.search.aclose()
        finally:
            try:
                await self.parser.close()
            finally:
                try:
                    await self.content.close()
                finally:
                    await self.cache.close()


class DebugExecution(PublicResearchExecution):
    def __init__(self, runtime):
        if runtime.settings.dr4a_env != "development":
            raise AppError("service_not_ready", "Debug executor requires development remote model")
        super().__init__(runtime)
