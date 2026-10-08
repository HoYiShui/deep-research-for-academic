from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.debug_runtime import DebugExecution, PublicResearchExecution
from application.errors import AppError
from application.settings import Settings
from cli.research_tools import ResearchDebugTools
from domain.ports import AdapterError
from domain.research.models import RunConfig
from infrastructure.clock import SystemClock
from infrastructure.parser.html import HTML_PARSER_VERSION
from infrastructure.search.arxiv import ArxivSearch
from infrastructure.search.bocha import BochaSearch
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


async def test_public_runtime_registers_only_web_without_constructing_arxiv(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dormant paper adapter must not be constructed")

    calls = []

    async def search(self, query):
        calls.append(query)
        return []

    monkeypatch.setattr(ArxivSearch, "__init__", forbidden)
    monkeypatch.setattr(BochaSearch, "search", search)
    runtime = SimpleNamespace(
        settings=Settings(parser_version=HTML_PARSER_VERSION),
        repository_store=SimpleNamespace(),
        llm=SimpleNamespace(),
        clock=SystemClock(),
        run_event_bus=SimpleNamespace(emit=lambda event: None),
    )
    execution = PublicResearchExecution(runtime)
    try:
        assert [provider.name for provider in execution.driver.search.providers] == ["bocha"]
        batch = await execution.search.search_batch(
            "academic research", categories=frozenset({"papers", "web"})
        )
        assert [outcome.source for outcome in batch.outcomes] == ["bocha"]
        assert calls == ["academic research"]
        with pytest.raises(AdapterError, match="No authorized search source"):
            await execution.search.search_batch("paper-only", categories=frozenset({"papers"}))
        assert calls == ["academic research"]  # No substitution of web for papers-only.
    finally:
        await execution.aclose()


async def test_independent_phase_tools_also_leave_paper_adapter_dormant(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Dormant paper adapter must not be constructed")

    async def search(self, query):
        return []

    monkeypatch.setattr(ArxivSearch, "__init__", forbidden)
    monkeypatch.setattr(BochaSearch, "search", search)
    settings = Settings(parser_version=HTML_PARSER_VERSION)
    config = RunConfig.model_validate(settings.run_config_snapshot())
    tools = ResearchDebugTools(settings, config, {"search_calls": 0}, fake=False)
    try:
        tools.for_unit(
            SimpleNamespace(
                unit_id="query-test", parameters={"kind": "query", "query": "test"}
            )
        )
        result = await tools.invoke("search", {"query": "test"})
        assert [item["source"] for item in result["outcomes"]] == ["bocha"]
        assert tools.usage["search_calls"] == 1
    finally:
        await tools.close()
