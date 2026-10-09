"""Exercise public failures through HTTP, without invoking services directly."""

from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from application.errors import AppError
from domain.ports import AdapterError
from interface.http_errors import install_http_errors


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: UUID


@pytest.fixture
def client():
    app = FastAPI()
    install_http_errors(app)

    @app.post("/input")
    async def validate(body: Input):
        return {"resource_id": str(body.resource_id)}

    @app.get("/failure/{kind}")
    async def fail(kind: str):
        if kind == "stale":
            raise AppError(
                "stale_brief", "Read the current brief", details={"current_brief_version": 3}
            )
        if kind == "pending":
            raise AppError("request_in_progress", "Request is in progress", retryable=True)
        if kind == "lease":
            raise AppError(
                "request_in_progress",
                "Request is in progress",
                retryable=True,
                details={"retry_after_s": 91},
            )
        if kind == "adapter":
            raise AdapterError("postgres", "connection_failed", "secret-db-url", True, "read")
        if kind == "http":
            raise HTTPException(401, detail="secret-token")
        if kind == "unknown":
            raise AppError("unknown_internal_code", "secret-value")
        raise RuntimeError("secret-stack-and-password")

    with TestClient(app, raise_server_exceptions=False) as http:
        yield http


def check_error(response, status, code):
    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == {"code", "message", "request_id", "retryable", "details"}
    assert error["code"] == code
    UUID(error["request_id"])
    assert response.headers["X-Request-ID"] == error["request_id"]
    assert "secret" not in response.text
    return error


@pytest.mark.parametrize(
    "body", [{"resource_id": "secret-invalid-id"}, {"extra": "secret-password"}]
)
def test_validation_is_safe_and_uniform(client, body):
    error = check_error(client.post("/input", json=body), 422, "validation_error")
    assert error["details"]["fields"]


def test_malformed_json_is_400(client):
    check_error(
        client.post("/input", content='{ "secret":', headers={"Content-Type": "application/json"}),
        400,
        "malformed_json",
    )


@pytest.mark.parametrize(
    "kind,status,code",
    [
        ("stale", 409, "stale_brief"),
        ("pending", 409, "request_in_progress"),
        ("adapter", 503, "dependency_unavailable"),
        ("http", 401, "unauthenticated"),
        ("unknown", 500, "internal_error"),
        ("unexpected", 500, "internal_error"),
    ],
)
def test_failure_mapping(client, kind, status, code):
    response = client.get("/failure/" + kind)
    check_error(response, status, code)
    if kind == "pending":
        assert int(response.headers["Retry-After"]) > 0


def test_not_found_uses_same_envelope(client):
    check_error(client.get("/missing"), 404, "not_found")


def test_retry_after_matches_repository_lease(client):
    response = client.get("/failure/lease")
    check_error(response, 409, "request_in_progress")
    assert response.headers["Retry-After"] == "91"


def test_lifespan_scopes_container_and_development_identity(tmp_path, monkeypatch):
    from fastapi import Depends, Request

    from application.bootstrap import get_container
    from application.settings import Settings
    from interface.deps import require_user
    from interface.main import create_app

    settings = Settings.load(env_file=tmp_path / "missing", environ={})
    created = []

    class OwnedContainer:
        closed = False

        async def aclose(self):
            self.closed = True

    def factory(config):
        assert config is settings
        container = OwnedContainer()
        created.append(container)
        return container

    apps = [create_app(settings=settings, container_factory=factory) for _ in range(2)]
    for app in apps:

        @app.get("/identity")
        async def identity(request: Request, user=Depends(require_user)):  # noqa: B008
            assert not get_container(request).closed
            return {"user_id": user}

    with TestClient(apps[0]) as first, TestClient(apps[1]) as second:
        assert created[0] is not created[1]
        # Configuration does not drift after startup or load per request.
        monkeypatch.setenv("DR4A_AUTH_REQUIRED", "true")
        for client in (first, second):
            response = client.get("/identity")
            assert response.status_code == 200
            assert response.json()["user_id"] == "00000000-0000-4000-8000-000000000001"
    assert all(container.closed for container in created)
    assert all(not hasattr(app.state, "container") for app in apps)


def test_startup_rejects_production_anonymous_before_composition(monkeypatch):
    from pydantic import ValidationError

    from interface.main import create_app

    monkeypatch.setenv("DR4A_ENV", "production")
    monkeypatch.setenv("DR4A_AUTH_REQUIRED", "false")

    def factory(config):
        pytest.fail("Unsafe settings reached the composition root")

    with (
        pytest.raises(ValidationError, match="production requires authentication"),
        TestClient(create_app(container_factory=factory)),
    ):
        pass


def test_request_id_is_server_generated_for_success_and_failure(client):
    headers = {"X-Request-ID": "secret-untrusted-caller-value"}
    success = client.post(
        "/input", json={"resource_id": "00000000-0000-4000-8000-000000000001"}, headers=headers
    )
    UUID(success.headers["X-Request-ID"])
    failure = client.get("/failure/stale", headers=headers)
    check_error(failure, 409, "stale_brief")
    assert success.headers["X-Request-ID"] != failure.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/research", {"query": "question", "unknown_field": "secret-value"}),
        (
            "/auth/register",
            {"email": "test@example.org", "password": "secret-password", "unknown_field": True},
        ),
        (
            "/auth/login",
            {"email": "test@example.org", "password": "secret-password", "unknown_field": True},
        ),
        ("/knowledge-bases", {"name": "reference", "unknown_field": "secret-value"}),
    ],
)
def test_actual_app_rejects_unknown_request_fields(monkeypatch, path, body):
    from interface.main import create_app

    monkeypatch.setenv("DR4A_ENV", "development")
    monkeypatch.setenv("DR4A_AUTH_REQUIRED", "false")

    # No service or storage operation is allowed for invalid requests.
    class ValidationRuntime:
        async def aclose(self):
            pass

    app = create_app(container_factory=lambda config: ValidationRuntime())
    with TestClient(app, raise_server_exceptions=False) as http:
        check_error(
            http.post(path, json=body, headers={"Idempotency-Key": "invalid-body-test"}),
            422,
            "validation_error",
        )


async def test_container_closes_unique_adapters_even_after_failure():
    from application.bootstrap import Container

    closed = []

    class Adapter:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail

        async def aclose(self):
            closed.append(self.name)
            if self.fail:
                raise RuntimeError("Test shutdown failure")

    container = object.__new__(Container)
    container.store = Adapter("store", fail=True)
    container.llm = Adapter("llm")
    container.search = container.llm
    container.vector = Adapter("vector")
    container.execution = Adapter("execution")
    with pytest.raises(ExceptionGroup, match="Adapter shutdown failed"):
        await container.aclose()
    assert closed == ["store", "llm", "vector", "execution"]


async def test_legacy_pool_shutdown_is_bounded_and_owned():
    from infrastructure.storage.postgres import PostgresStateStore

    class Pool:
        terminated = False

        async def close(self):
            raise TimeoutError("Test timeout")

        def terminate(self):
            self.terminated = True

    store = PostgresStateStore("unused-test-dsn")
    pool = Pool()
    store._pool = pool
    await store.aclose()
    assert pool.terminated
    assert store._pool is None
    await store.aclose()
