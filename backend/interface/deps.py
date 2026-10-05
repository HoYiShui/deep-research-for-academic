"""FastAPI dependencies: resolve the authenticated user from the JWT."""

from __future__ import annotations

from fastapi import HTTPException, Request

from application.bootstrap import get_container
from application.settings import Settings


async def require_user(request: Request) -> str:
    """Resolve the caller identity, or use the local development identity.

    Authentication is intentionally disabled by default while the research
    workflow is being validated through the local TUI.  Production compose sets
    ``DR4A_AUTH_REQUIRED=true`` and retains the original JWT/cookie guard.
    """
    settings = getattr(request.app.state, "settings", None) or Settings.load()
    if not settings.dr4a_auth_required:
        return "00000000-0000-4000-8000-000000000001"
    token = _extract_token(request)
    user_id = get_container(request).auth.verify_token(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user_id


def _extract_token(request: Request) -> str | None:
    """Extract the JWT from the Authorization header or the access_token cookie."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :]
    return request.cookies.get("access_token")
