from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.debug_runtime import DebugExecution, PublicResearchExecution
from application.errors import AppError
from application.settings import Settings
from application.web_search import web_search_binding
from cli.research_tools import ResearchDebugTools
from domain.research.models import RunConfig
from infrastructure.clock import SystemClock
from infrastructure.parser.html import HTML_PARSER_VERSION
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.openalex import OpenAlexSearch
from infrastructure.search.search_router import SearchRouterSearch
from scripts.debug_backend import debug_settings


def test_debug_profile_changes_only_database_and_explicit_runtime_choices():
    source = Settings(database_url="postgresql://owner:secret@127.0.0.1:5432/deepresearch")
    debug = debug_settings(source)
    assert debug.database_url.get_secret_value().endswith("/dr4a_debug")
    assert source.database_url.get_secret_value().endswith("/deepresearch")
    assert debug.parser_version == HTML_PARSER_VERSION
    assert not debug.dr4a_debug_runner
    assert debug_settings(source, execute=True).dr4a_debug_runner


def test_debug_runner_is_not_a_production_fallback():
    with pytest.raises(ValidationError, match="debug runner is development-only"):
        Settings(dr4a_env="production", dr4a_debug_runner=True)
    with pytest.raises(ValueError, match="anonymous development"):
        debug_settings(Settings(dr4a_auth_required=True))


def test_database_only_debug_profile_preserves_explicit_parser_and_connection_options():
    source = Settings(
        database_url="postgresql://owner:secret@127.0.0.1:5432/original?sslmode=require",
        parser_version="explicit-parser-version",
    )
    debug = debug_settings(source, parser=None)
    assert debug.database_url.get_secret_value() == (
        "postgresql://owner:secret@127.0.0.1:5432/dr4a_debug?sslmode=require"
    )
    assert debug.parser_version == source.parser_version
    assert debug_settings(debug, parser=None) == debug
    assert source.database_url.get_secret_value().endswith("/original?sslmode=require")


def test_http_debug_wrapper_still_rejects_production_before_adapter_construction():
    runtime = SimpleNamespace(settings=SimpleNamespace(dr4a_env="production"))
    with pytest.raises(AppError, match="development"):
        DebugExecution(runtime)


async def test_public_runtime_registers_web_and_papers_by_category(monkeypatch):
    calls = []

    def recorder(name):
        async def search(self, query):
            calls.append((name, query))
            return []

        return search

    monkeypatch.setattr(OpenAlexSearch, "search", recorder("openalex"))
    monkeypatch.setattr(SearchRouterSearch, "search", recorder("search_router"))
    runtime = SimpleNamespace(
        settings=Settings(parser_version=HTML_PARSER_VERSION),
        repository_store=SimpleNamespace(),
        llm=SimpleNamespace(),
        clock=SystemClock(),
        run_event_bus=SimpleNamespace(emit=lambda event: None),
    )
    execution = PublicResearchExecution(runtime)
    try:
        assert [provider.name for provider in execution.driver.search.providers] == [
            "search_router",
            "openalex",
        ]
        batch = await execution.search.search_batch(
            "academic research", categories=frozenset({"papers", "web"})
        )
        assert sorted(outcome.source for outcome in batch.outcomes) == ["openalex", "search_router"]
        calls.clear()
        await execution.search.search_batch("paper-only", categories=frozenset({"papers"}))
        assert calls == [("openalex", "paper-only")]  # No substitution of web for papers-only.
    finally:
        await execution.aclose()


async def test_paper_search_can_be_disabled(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled paper adapter must not be constructed")

    monkeypatch.setattr(ArxivSearch, "__init__", forbidden)
    monkeypatch.setattr(OpenAlexSearch, "__init__", forbidden)
    binding = web_search_binding(
        Settings(parser_version=HTML_PARSER_VERSION, paper_search_provider="none")
    )
    try:
        assert [provider.name for provider in binding.providers] == ["search_router"]
    finally:
        await binding.adapter.aclose()


async def test_independent_phase_tools_search_web_and_papers(monkeypatch):
    async def search(self, query):
        return []

    monkeypatch.setattr(OpenAlexSearch, "search", search)
    monkeypatch.setattr(SearchRouterSearch, "search", search)
    settings = Settings(parser_version=HTML_PARSER_VERSION)
    config = RunConfig.model_validate(settings.run_config_snapshot())
    tools = ResearchDebugTools(settings, config, {"search_calls": 0}, fake=False)
    try:
        tools.for_unit(
            SimpleNamespace(unit_id="query-test", parameters={"kind": "query", "query": "test"})
        )
        result = await tools.invoke("search", {"query": "test"})
        assert sorted(item["source"] for item in result["outcomes"]) == ["openalex", "search_router"]
        assert tools.usage["search_calls"] == 2
    finally:
        await tools.close()
