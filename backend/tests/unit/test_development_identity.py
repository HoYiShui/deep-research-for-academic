"""Development identity is a persisted, reserved identity, never a login."""

import asyncio
from uuid import uuid4

import pytest

from application.errors import AppError
from application.identity import ensure_development_identity
from application.records import DEVELOPMENT_USER_ID, User
from infrastructure.fake_research import FakeClock, FakeResearchDatabase


async def test_development_user_is_idempotent_and_has_no_credentials():
    clock = FakeClock()
    db = FakeResearchDatabase(clock)
    original = await ensure_development_identity(db, db.users, clock)
    clock.advance(60)
    results = await asyncio.gather(
        *(ensure_development_identity(db, db.users, clock) for _ in range(4))
    )
    assert all(result == original for result in results)
    assert original.user_id == DEVELOPMENT_USER_ID
    assert original.is_development
    assert original.password_hash is None
    assert await db.users.get_by_id(DEVELOPMENT_USER_ID) == original


async def test_reserved_id_collision_is_not_overwritten():
    clock, db = FakeClock(), FakeResearchDatabase()
    occupied = User(
        user_id=DEVELOPMENT_USER_ID,
        email="registered@example.org",
        password_hash="registered-hash",
        is_development=False,
        created_at=clock.now_utc(),
    )
    async with db.transaction() as tx:
        await db.users.create(occupied, tx)
    with pytest.raises(AppError, match="service_not_ready"):
        await ensure_development_identity(db, db.users, clock)
    assert await db.users.get_by_id(DEVELOPMENT_USER_ID) == occupied


async def test_reserved_email_collision_does_not_reuse_another_owner():
    clock, db = FakeClock(), FakeResearchDatabase()
    occupied = User(
        user_id=uuid4(),
        email="development@dr4a.invalid",
        password_hash="registered-hash",
        is_development=False,
        created_at=clock.now_utc(),
    )
    async with db.transaction() as tx:
        await db.users.create(occupied, tx)
    with pytest.raises(AppError, match="service_not_ready"):
        await ensure_development_identity(db, db.users, clock)
    assert await db.users.get_by_id(DEVELOPMENT_USER_ID) is None
    assert await db.users.get_by_id(occupied.user_id) == occupied
