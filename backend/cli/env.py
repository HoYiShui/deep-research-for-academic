"""Shared environment loading for host-side CLI commands."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from application.settings import BACKEND_ENV, Settings


def load_backend_env(path: Path | None = None) -> None:
    """Load ``backend/.env`` without overriding explicitly exported variables."""
    env_path = path or BACKEND_ENV
    if not env_path.exists():
        return
    Settings.load(env_file=env_path)
    for key, value in dotenv_values(env_path, interpolate=False).items():
        if value is not None:
            os.environ.setdefault(key, value)
