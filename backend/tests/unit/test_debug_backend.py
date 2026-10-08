from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from application.debug_runtime import DebugExecution
from application.errors import AppError
from application.settings import Settings
from infrastructure.parser.html import HTML_PARSER_VERSION
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
