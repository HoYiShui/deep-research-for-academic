"""Unit tests for the doctor command (T002)."""

from cli.commands import doctor


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
