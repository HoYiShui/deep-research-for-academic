"""Integration tests for the auth guard (T052)."""

from unittest.mock import patch

import jwt

from application.auth_service import AuthService
from infrastructure.storage.memory import InMemoryUserStore

_SECRET = "a" * 32


def _container() -> AuthService:
    return AuthService(InMemoryUserStore(), secret=_SECRET)


def test_protected_route_rejects_missing_token() -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    with patch("interface.deps.get_container", return_value=type("C", (), {"auth": _container()})()):
        client = TestClient(app)
        assert client.get("/research/s1").status_code == 401
        assert client.get("/knowledge-base/documents").status_code == 401


def test_protected_route_rejects_invalid_token() -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    with patch("interface.deps.get_container", return_value=type("C", (), {"auth": _container()})()):
        client = TestClient(app)
        headers = {"Authorization": "Bearer not-a-real-token"}
        assert client.get("/research/s1", headers=headers).status_code == 401


def test_protected_route_accepts_valid_token() -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    token = jwt.encode({"sub": "user-1"}, _SECRET, algorithm="HS256")
    container = type("C", (), {"auth": _container()})()

    class _Research:
        async def get_status(self, session_id: str) -> dict:
            return {"session_id": session_id, "status": "clarify"}

    container.research = _Research()

    with (
        patch("interface.deps.get_container", return_value=container),
        patch("interface.router.research.get_container", return_value=container),
    ):
        client = TestClient(app)
        resp = client.get("/research/s1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
