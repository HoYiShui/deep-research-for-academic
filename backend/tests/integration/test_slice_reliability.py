"""Integration tests for the reliability slice (T041): sandbox + auth.

Verifies: the sandbox failure semantics (empty output -> execution_status
failed), and the auth flow (bcrypt hash, login JWT, token verification).
"""

from unittest.mock import patch

import pytest

from application.auth_service import AuthService
from domain.research.agents import code_crafter
from infrastructure.storage.memory import InMemoryUserStore


class _EmptyExecution:
    """Sandbox that returns no result (e.g. timeout or missing result.json)."""

    async def execute(self, code: str, input_data: dict, timeout_s: int = 30) -> dict:
        return {}


@pytest.mark.asyncio
async def test_code_crafter_marks_failed_on_empty_sandbox_output() -> None:
    artifact = await code_crafter.analyze([], _EmptyExecution())
    assert artifact["execution_status"] == "failed"


@pytest.mark.asyncio
async def test_register_hashes_password_and_login_issues_verifiable_token() -> None:
    users = InMemoryUserStore()
    auth = AuthService(users, secret="a" * 32)
    await auth.register("a@b.com", "hunter2")

    stored = await users.get_by_email("a@b.com")
    assert stored["password_hash"] != "hunter2"  # never plaintext

    token = await auth.login("a@b.com", "hunter2")
    assert auth.verify_token(token["access_token"]) == stored["user_id"]


@pytest.mark.asyncio
async def test_login_rejects_wrong_password() -> None:
    auth = AuthService(InMemoryUserStore(), secret="a" * 32)
    await auth.register("a@b.com", "right")
    with pytest.raises(ValueError):
        await auth.login("a@b.com", "wrong")


@pytest.mark.asyncio
async def test_register_duplicate_email_raises() -> None:
    auth = AuthService(InMemoryUserStore(), secret="a" * 32)
    await auth.register("a@b.com", "pw")
    with pytest.raises(ValueError):
        await auth.register("a@b.com", "other")


def test_auth_endpoints() -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    class _Container:
        auth = AuthService(InMemoryUserStore(), secret="a" * 32)

    with patch("interface.router.auth.get_container", return_value=_Container()):
        client = TestClient(app)
        reg = client.post("/auth/register", json={"email": "a@b.com", "password": "password123"})
        assert reg.status_code == 201
        login = client.post("/auth/login", json={"email": "a@b.com", "password": "password123"})
        assert login.status_code == 200
        assert "access_token" in login.json()


def test_auth_endpoints_reject_invalid_body() -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    class _Container:
        auth = AuthService(InMemoryUserStore(), secret="a" * 32)

    with patch("interface.router.auth.get_container", return_value=_Container()):
        client = TestClient(app)
        # Missing field and short password must yield 422, not a bare dict.
        assert client.post("/auth/register", json={"email": "a@b.com"}).status_code == 422
        assert client.post("/auth/register", json={"email": "a@b.com", "password": "pw"}).status_code == 422
