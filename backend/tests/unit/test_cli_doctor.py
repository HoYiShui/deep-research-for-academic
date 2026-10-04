"""Unit tests for the doctor command (T002)."""

import os
from pathlib import Path

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


def test_check_model_weights_accepts_hf_name(monkeypatch) -> None:
    monkeypatch.setenv("BGE_M3_MODEL_PATH", "BAAI/bge-m3")
    assert doctor._check_model_weights() is True


def test_check_model_weights_flags_missing_local_dir(monkeypatch) -> None:
    monkeypatch.setenv("BGE_M3_MODEL_PATH", "/nonexistent/dir/bge-m3")
    assert doctor._check_model_weights() is False


def test_load_backend_env_populates_missing_values_without_overriding_exports(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / ".env"
    path.write_text("DATABASE_URL=from-file\nBOCHA_API_KEY=from-file\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BOCHA_API_KEY", "exported")

    load_backend_env(path)

    assert os.environ["DATABASE_URL"] == "from-file"
    assert os.environ["BOCHA_API_KEY"] == "exported"
