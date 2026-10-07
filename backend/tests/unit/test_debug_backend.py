import pytest
from pydantic import ValidationError

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
