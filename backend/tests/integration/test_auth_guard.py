"""Integration tests for the auth guard (T052)."""

from unittest.mock import patch
from uuid import UUID

import jwt

from application.auth_service import AuthService
from infrastructure.storage.memory import InMemoryUserStore

_SECRET = "a" * 32


def _container() -> AuthService:
    return AuthService(InMemoryUserStore(), secret=_SECRET)


def test_protected_route_rejects_missing_token_when_authentication_is_enabled(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    monkeypatch.setenv("DR4A_AUTH_REQUIRED", "true")

    with patch(
        "interface.deps.get_container", return_value=type("C", (), {"auth": _container()})()
    ):
        client = TestClient(app)
        assert client.get("/research/s1").status_code == 401
        assert client.get("/knowledge-bases").status_code == 401


def test_protected_route_rejects_invalid_token_when_authentication_is_enabled(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    monkeypatch.setenv("DR4A_AUTH_REQUIRED", "true")

    with patch(
        "interface.deps.get_container", return_value=type("C", (), {"auth": _container()})()
    ):
        client = TestClient(app)
        headers = {"Authorization": "Bearer not-a-real-token"}
        assert client.get("/research/s1", headers=headers).status_code == 401


def test_protected_route_accepts_valid_token_when_authentication_is_enabled(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    monkeypatch.setenv("DR4A_AUTH_REQUIRED", "true")

    user_id = "00000000-0000-4000-8000-000000000002"
    session_id = "00000000-0000-4000-8000-000000000003"
    token = jwt.encode({"sub": user_id}, _SECRET, algorithm="HS256")
    container = type("C", (), {"auth": _container()})()

    class _Queries:
        async def session_view(self, owner: UUID, resource_id: UUID) -> dict:
            assert owner == UUID(user_id)
            assert resource_id == UUID(session_id)
            return {"session_id": str(resource_id), "status": "confirm"}

    container.research_queries = _Queries()

    with (
        patch("interface.deps.get_container", return_value=container),
        patch("interface.router.research.get_container", return_value=container),
    ):
        client = TestClient(app)
        resp = client.get(f"/research/{session_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_local_development_mode_does_not_call_auth_service(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    monkeypatch.delenv("DR4A_AUTH_REQUIRED", raising=False)

    class _Queries:
        async def session_view(self, owner: UUID, session_id: UUID) -> dict:
            assert owner == UUID("00000000-0000-4000-8000-000000000001")
            return {"session_id": str(session_id), "status": "confirm"}

    container = type("C", (), {"research_queries": _Queries()})()
    with patch("interface.router.research.get_container", return_value=container):
        response = TestClient(app).get("/research/00000000-0000-4000-8000-000000000003")
    assert response.status_code == 200


def test_non_uuid_token_identity_is_rejected_before_business(monkeypatch):
    from fastapi.testclient import TestClient

    from interface.main import app

    monkeypatch.setenv("DR4A_AUTH_REQUIRED", "true")
    token = jwt.encode({"sub": "legacy-non-uuid-user"}, _SECRET, algorithm="HS256")
    container = type("C", (), {"auth": _container()})()
    with patch("interface.deps.get_container", return_value=container):
        response = TestClient(app).get(
            "/research/00000000-0000-4000-8000-000000000003",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
