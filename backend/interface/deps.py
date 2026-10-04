"""FastAPI dependencies: resolve the authenticated user from the JWT."""

from __future__ import annotations

import os

from fastapi import HTTPException, Request

from application.bootstrap import get_container


async def require_user(request: Request) -> str:
    """Resolve the caller identity, or use the local development identity.

    Authentication is intentionally disabled by default while the research
    workflow is being validated through the local TUI.  Production compose sets
    ``DR4A_AUTH_REQUIRED=true`` and retains the original JWT/cookie guard.
    """
    if not _authentication_required():
        return "development-user"
    token = _extract_token(request)
    user_id = get_container().auth.verify_token(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user_id


def _authentication_required() -> bool:
    """Read the explicit opt-in guard without loading secrets into the client."""
    return os.environ.get("DR4A_AUTH_REQUIRED", "false").lower() in {"1", "true", "yes"}


def _extract_token(request: Request) -> str | None:
    """Extract the JWT from the Authorization header or the access_token cookie."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :]
    return request.cookies.get("access_token")
