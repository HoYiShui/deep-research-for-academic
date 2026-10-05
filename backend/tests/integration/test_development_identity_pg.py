"""Reserved development-user creation against real, isolated PostgreSQL."""

import asyncio
from uuid import uuid4

import pytest

from application.errors import AppError
from application.identity import ensure_development_identity
from application.records import DEVELOPMENT_EMAIL, DEVELOPMENT_USER_ID, DevelopmentUser, User
from infrastructure.clock import SystemClock
from infrastructure.storage.migrations import run_migrations
from infrastructure.storage.research_postgres import PostgresResearchStore


async def test_concurrent_development_startup_keeps_one_identity(pg_database):
    pool, _ = pg_database
    await run_migrations(pool)
    stores = [PostgresResearchStore(pool) for _ in range(4)]
    clock = SystemClock()
    users = await asyncio.gather(
        *(ensure_development_identity(store, store.users, clock) for store in stores)
    )
    assert all(user == users[0] for user in users)
    assert users[0].user_id == DEVELOPMENT_USER_ID
    assert await pool.fetchval("SELECT count(*) FROM users") == 1
    row = await pool.fetchrow("SELECT * FROM users WHERE user_id=$1", DEVELOPMENT_USER_ID)
    assert row["password_hash"] is None
    assert row["is_development"]


@pytest.mark.parametrize("collision", ["id", "email"])
async def test_reserved_collision_fails_without_mutating_user(pg_database, collision):
    pool, _ = pg_database
    await run_migrations(pool)
    store, clock = PostgresResearchStore(pool), SystemClock()
    occupied = User(
        user_id=DEVELOPMENT_USER_ID if collision == "id" else uuid4(),
        email=DEVELOPMENT_EMAIL if collision == "email" else "registered@example.org",
        password_hash="fixture-hash",
        is_development=False,
        created_at=clock.now_utc(),
    )
    async with store.transaction() as tx:
        await store.users.create(occupied, tx)
    with pytest.raises(AppError, match="service_not_ready"):
        await ensure_development_identity(store, store.users, clock)
    assert await store.users.get_by_id(occupied.user_id) == occupied
    assert await pool.fetchval("SELECT count(*) FROM users") == 1


async def test_development_creation_uses_callers_transaction(pg_database):
    pool, _ = pg_database
    await run_migrations(pool)
    store = PostgresResearchStore(pool)
    user = DevelopmentUser(
        user_id=DEVELOPMENT_USER_ID,
        email=DEVELOPMENT_EMAIL,
        password_hash=None,
        is_development=True,
        created_at=SystemClock().now_utc(),
    )
    with pytest.raises(RuntimeError, match="Injected rollback"):
        async with store.transaction() as tx:
            await store.users.ensure_development(user, tx)
            assert await store.users.get_by_id(DEVELOPMENT_USER_ID, tx) is not None
            raise RuntimeError("Injected rollback")
    assert await store.users.get_by_id(DEVELOPMENT_USER_ID) is None
