"""Shared environment loading for host-side CLI commands."""

from __future__ import annotations

import os
from pathlib import Path


def load_backend_env(path: Path | None = None) -> None:
    """Load ``backend/.env`` without overriding explicitly exported variables."""
    env_path = path or Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
