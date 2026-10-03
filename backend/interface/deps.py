"""FastAPI dependencies: resolve the authenticated user from the JWT."""

from __future__ import annotations

from fastapi import HTTPException, Request

from application.bootstrap import get_container


async def require_user(request: Request) -> str:
    """Resolve the authenticated user_id from the Bearer token or cookie.

    Raises:
        HTTPException 401 when the token is missing or invalid.
    """
    token = _extract_token(request)
    user_id = get_container().auth.verify_token(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user_id


def _extract_token(request: Request) -> str | None:
    """Extract the JWT from the Authorization header or the access_token cookie."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :]
    return request.cookies.get("access_token")
