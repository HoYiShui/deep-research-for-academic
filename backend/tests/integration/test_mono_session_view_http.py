"""Public GET SessionView through ASGI HTTP and real isolated PostgreSQL."""

from uuid import uuid4

import httpx

from application.identity import ensure_development_identity
from application.records import DEVELOPMENT_USER_ID, User
from application.research_queries import ResearchQueries
from application.settings import Settings
from infrastructure.clock import SystemClock
from infrastructure.storage.migrations import run_migrations
from infrastructure.storage.research_postgres import PostgresResearchStore
from interface.main import create_app
from tests.integration.test_mono_transactions import candidate, freezing


async def test_public_owner_scoped_views_and_persisted_freeze(pg_database, tmp_path):
    pool, _ = pg_database
    await run_migrations(pool)
    store, clock = PostgresResearchStore(pool), SystemClock()
    settings = Settings.load(env_file=tmp_path / "missing", environ={})

    class Runtime:
        def __init__(self):
            self.research_queries = ResearchQueries(store, store.research)
            self.closed = False

        async def prepare(self):
            await ensure_development_identity(store, store.users, clock)

        async def aclose(self):
            self.closed = True

    runtime = Runtime()
    app = create_app(settings=settings, container_factory=lambda config: runtime)
    async with app.router.lifespan_context(app):
        assert await store.users.get_by_id(DEVELOPMENT_USER_ID) is not None
        owner = User(
            user_id=uuid4(),
            email="other@example.org",
            password_hash="fixture-hash",
            is_development=False,
            created_at=clock.now_utc(),
        )
        own, other = candidate(DEVELOPMENT_USER_ID), candidate(owner.user_id)
        async with store.transaction() as tx:
            await store.users.create(owner, tx)
            await store.research.commit_session_change(0, own, tx)
            await store.research.commit_session_change(0, other, tx)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await http.get(f"/research/{own.session.session_id}")
            assert response.status_code == 200
            view = response.json()
            assert view["status"] == "confirm"
            assert view["research_brief"] == own.brief.content.model_dump(mode="json")
            assert view["run_id"] is None and view["sse_url"] is None
            assert view["checkpoint_seq"] is None and view["phase"] is None
            assert "owner_id" not in view and "query" not in view
            assert response.headers["X-Request-ID"]
            assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
            for identifier in (other.session.session_id, uuid4()):
                response = await http.get(f"/research/{identifier}")
                assert response.status_code == 404
                assert response.json()["error"]["code"] == "session_not_found"
                assert "other@example.org" not in response.text
            assert (await http.get("/research/not-a-uuid")).status_code == 422
            frozen = freezing(own)
            async with store.transaction() as tx:
                await store.research.freeze_and_create_run(frozen, tx)
            response = await http.get(f"/research/{own.session.session_id}")
            assert response.status_code == 200
            view = response.json()
            assert view["status"] == "ready" and view["phase"] == "plan"
            assert view["run_id"] == str(frozen.run.run_id)
            assert view["checkpoint_seq"] == 1
            assert view["research_brief"] == own.brief.content.model_dump(mode="json")
            assert view["sse_url"] == f"/research/{own.session.session_id}/events"
            assert "lease_owner" not in view and "config_snapshot" not in view
            # Reading a ready session does not claim or execute its Run.
            assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 0
            assert await pool.fetchval("SELECT status FROM research_runs") == "ready"
            # A corrupt status pair must not be presented as a successful run.
            await pool.execute(
                "UPDATE sessions SET status='running' WHERE session_id=$1", own.session.session_id
            )
            response = await http.get(f"/research/{own.session.session_id}")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "dependency_unavailable"
    assert runtime.closed


async def test_unassembled_read_path_is_explicitly_unavailable(tmp_path):
    class Runtime:
        async def aclose(self):
            pass

    app = create_app(
        settings=Settings.load(env_file=tmp_path / "missing", environ={}),
        container_factory=lambda config: Runtime(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        response = await http.get(f"/research/{uuid4()}")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "service_not_ready"
