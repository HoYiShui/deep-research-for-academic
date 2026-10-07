"""Unit tests for the doctor command (T002)."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

from application.settings import Settings
from cli.commands import doctor
from cli.env import load_backend_env


def test_check_env_detects_missing_keys(monkeypatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "BOCHA_API_KEY", "DATABASE_URL", "JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert doctor._check_env() is False


def test_check_env_passes_when_keys_present(monkeypatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "BOCHA_API_KEY", "DATABASE_URL", "JWT_SECRET"):
        monkeypatch.setenv(k, "x")
    assert doctor._check_env() is True


def test_check_model_weights_rejects_unprepared_hf_name(monkeypatch) -> None:
    monkeypatch.setenv("BGE_M3_MODEL_PATH", "BAAI/bge-m3")
    assert doctor._check_model_weights() is False


def test_check_env_rejects_blank_credentials(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "BOCHA_API_KEY", "DATABASE_URL", "JWT_SECRET"):
        monkeypatch.setenv(key, " ")
    assert not doctor._check_env()


async def test_research_scope_uses_settings_debug_database_and_excludes_kb(monkeypatch, capsys):
    settings = Settings(
        database_url="postgresql://owner:private@localhost:5432/original",
        anthropic_api_key="configured",
        bocha_api_key="configured",
        minio_access_key="configured",
        minio_secret_key="configured",
    )
    monkeypatch.setattr(Settings, "load", lambda: settings)
    seen = []

    async def postgres(config):
        seen.append(config.database_url.get_secret_value())
        return True, True

    async def minio(config):
        return True

    async def forbidden(*args):
        raise AssertionError("Research scope must not probe KB infrastructure")

    monkeypatch.setattr(doctor, "_postgres_checks", postgres)
    monkeypatch.setattr(doctor, "_check_bucket", minio)
    monkeypatch.setattr(doctor, "_check_milvus", forbidden)
    result = await doctor.run(SimpleNamespace(json=True, scope="research", debug_db=True))
    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["scope"] == "research" and data["checks"]["postgres_schema"]
    assert "milvus" not in data["checks"] and "model_weights" not in data["checks"]
    assert seen == ["postgresql://owner:private@localhost:5432/dr4a_debug"]
    assert "private" not in json.dumps(data)
    assert data["limitations"]


async def test_connected_legacy_database_is_not_ready(monkeypatch, capsys):
    monkeypatch.setattr(Settings, "load", lambda: Settings())

    async def postgres(config):
        return True, False

    async def minio(config):
        return False

    monkeypatch.setattr(doctor, "_postgres_checks", postgres)
    monkeypatch.setattr(doctor, "_check_bucket", minio)
    assert await doctor.run(SimpleNamespace(json=True, scope="research", debug_db=False)) == 3
    data = json.loads(capsys.readouterr().out)
    assert data["checks"]["postgres"] and not data["checks"]["postgres_schema"]
    assert data["error"]["code"] == "service_not_ready"


def test_research_config_does_not_require_jwt_in_anonymous_development():
    settings = Settings(
        database_url="postgresql://owner:secret@localhost/db",
        anthropic_api_key="configured",
        bocha_api_key="configured",
        minio_access_key="configured",
        minio_secret_key="configured",
    )
    assert doctor._check_config(settings)
    assert not doctor._check_config(Settings())


def test_empty_weights_directory_is_not_a_model(tmp_path):
    assert not doctor._check_model_weights(str(tmp_path))


def test_check_model_weights_flags_missing_local_dir(monkeypatch) -> None:
    monkeypatch.setenv("BGE_M3_MODEL_PATH", "/nonexistent/dir/bge-m3")
    assert doctor._check_model_weights() is False


def test_load_backend_env_populates_missing_values_without_overriding_exports(
    monkeypatch, tmp_path: Path
) -> None:
    # load_backend_env mutates os.environ directly. Isolate its mapping so new
    # keys are restored too, even when delenv found no key to register for undo.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    path = tmp_path / ".env"
    path.write_text("DATABASE_URL=from-file\nBOCHA_API_KEY=from-file\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BOCHA_API_KEY", "exported")

    load_backend_env(path)

    assert os.environ["DATABASE_URL"] == "from-file"
    assert os.environ["BOCHA_API_KEY"] == "exported"
