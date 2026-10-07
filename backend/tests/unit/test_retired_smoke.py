"""Retired smoke entry points must fail before loading credentials or adapters."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("script", ["smoke_e2e", "smoke_real"])
@pytest.mark.parametrize("entry", ["module", "file"])
def test_retired_smoke_is_dependency_free_and_has_no_success_claim(script, entry):
    # -S excludes site-packages: these notices must work without loading the
    # application, adapters, .env, or any optional model runtime.
    command = [sys.executable, "-S"]
    command += ["-m", f"scripts.{script}"] if entry == "module" else [
        str(BACKEND / "scripts" / f"{script}.py")
    ]
    result = subprocess.run(
        command,
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": "credential-canary", "BOCHA_API_KEY": "key-canary"},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "retired" in result.stderr
    assert "verify_clarify_http" in result.stderr
    assert "verify_run_http" in result.stderr
    assert "cli doctor" in result.stderr
    assert "No network requests or database writes" in result.stderr
    assert "Traceback" not in result.stderr
    assert "credential-canary" not in result.stderr
    assert "key-canary" not in result.stderr
