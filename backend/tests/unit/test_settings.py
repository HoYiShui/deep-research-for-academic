"""Configuration precedence, safety, and reproducible run snapshots."""

import pytest
from pydantic import ValidationError

from application.settings import Settings


def test_precedence_and_dotenv_quoting(tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text('LLM_MODEL="file-model"\nRUN_DEADLINE_S=900\nBOCHA_API_KEY="file-secret"\n')
    settings = Settings.load(
        env_file=path,
        environ={"LLM_MODEL": "export-model", "RUN_DEADLINE_S": "1000"},
        overrides={"run_deadline_s": 1100},
    )
    assert settings.llm_model == "export-model"
    assert settings.run_deadline_s == 1100
    assert settings.bocha_api_key.get_secret_value() == "file-secret"


def test_loading_does_not_mutate_environment(monkeypatch, tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text("LLM_MODEL=only-in-file\n")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    Settings.load(env_file=path)
    import os

    assert "LLM_MODEL" not in os.environ


def test_defaults_are_host_endpoints(tmp_path) -> None:
    settings = Settings.load(env_file=tmp_path / "missing", environ={})
    assert settings.dr4a_env == "development"
    assert settings.dr4a_auth_required is False
    assert settings.milvus_uri == "http://localhost:19530"
    assert settings.minio_endpoint == "localhost:9000"
    assert settings.run_deadline_s == 1800
    assert settings.lease_s == 90


@pytest.mark.parametrize(
    "overrides",
    [
        {"dr4a_env": "production", "dr4a_auth_required": False},
        {"dr4a_env": "invalid"},
        {"run_deadline_s": 0},
        {"run_llm_calls": 7},
        {"run_tokens": 10000},
        {"heartbeat_s": 90},
        {"run_rework_rounds": -1},
        {"cors_allow_origins": ["*"]},
        {"llm_revision": ""},
        {"unknown_setting": True},
    ],
)
def test_invalid_config_is_rejected_without_exposing_secrets(tmp_path, overrides) -> None:
    with pytest.raises(ValidationError) as error:
        Settings.load(
            env_file=tmp_path / "missing",
            environ={"ANTHROPIC_API_KEY": "private-test-value"},
            overrides=overrides,
        )
    assert "private-test-value" not in str(error.value)


def test_production_requires_real_credentials(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Settings.load(
            env_file=tmp_path / "missing",
            environ={"DR4A_ENV": "production", "DR4A_AUTH_REQUIRED": "true"},
        )


def test_production_with_required_credentials(tmp_path) -> None:
    settings = Settings.load(
        env_file=tmp_path / "missing",
        environ={
            "DR4A_ENV": "production",
            "DR4A_AUTH_REQUIRED": "true",
            "JWT_SECRET": "a" * 40,
            "DATABASE_URL": "postgresql://example:db-secret@postgres:5432/example",
            "MINIO_ACCESS_KEY": "object-access",
            "MINIO_SECRET_KEY": "object-secret",
            "ANTHROPIC_API_KEY": "model-secret",
        },
    )
    assert settings.dr4a_auth_required


def test_snapshot_and_repr_exclude_credentials(tmp_path) -> None:
    settings = Settings.load(
        env_file=tmp_path / "missing",
        environ={
            "DATABASE_URL": "postgresql://user:db-secret@localhost/db",
            "ANTHROPIC_API_KEY": "model-secret",
            "BOCHA_API_KEY": "search-secret",
            "JWT_SECRET": "jwt-secret",
        },
    )
    snapshot = settings.run_config_snapshot()
    assert snapshot["versions"]["llm_model"] == settings.llm_model
    assert snapshot["limits"]["terminal_reserved_calls"] == 8
    for secret in ("db-secret", "model-secret", "search-secret", "jwt-secret"):
        assert secret not in repr(settings)
        assert secret not in settings.model_dump_json()
        assert secret not in str(snapshot)


def test_public_config_urls_cannot_embed_credentials(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Settings.load(
            env_file=tmp_path / "missing",
            environ={"ANTHROPIC_BASE_URL": "https://user:hidden@api.example.com"},
        )


def test_cors_json_and_private_snapshot(tmp_path) -> None:
    settings = Settings.load(
        env_file=tmp_path / "missing",
        environ={"CORS_ALLOW_ORIGINS": '["http://localhost:3000"]'},
    )
    assert settings.cors_allow_origins == ["http://localhost:3000"]
    snapshot = settings.run_config_snapshot(categories=["knowledge_base"], private_only=True)
    assert snapshot["source_policy"]["private_only"] is True


def test_http_startup_rejects_production_auth_bypass(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    monkeypatch.setenv("DR4A_ENV", "production")
    monkeypatch.setenv("DR4A_AUTH_REQUIRED", "false")
    with (
        pytest.raises(ValidationError, match="production requires authentication"),
        TestClient(app),
    ):
        pass


def test_composition_root_uses_explicit_settings(tmp_path) -> None:
    from application.bootstrap import Container
    from infrastructure.fake import FakeLLM

    settings = Settings.load(
        env_file=tmp_path / "missing",
        environ={"DATABASE_URL": "postgresql://user:pass@localhost/test_only"},
    )
    container = Container(llm=FakeLLM(), settings=settings)
    assert container.settings is settings
    assert container.store._dsn == settings.database_url.get_secret_value()


def test_local_llm_never_falls_back_to_external_adapter(tmp_path) -> None:
    from application.bootstrap import Container

    settings = Settings.load(
        env_file=tmp_path / "missing", environ={"LLM_LOCAL": "true"}
    )
    with pytest.raises(ValueError, match="local LLM adapter"):
        Container(settings=settings)
