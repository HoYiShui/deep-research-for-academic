"""Explicit real PostgreSQL fixtures; never reset the configured database."""

import asyncio
import re
from uuid import uuid4

import asyncpg
import pytest_asyncio

from application.settings import Settings


@pytest_asyncio.fixture
async def pg_database():
    """Create and remove only this invocation's uniquely named test database."""
    settings = Settings.load()
    dsn = settings.database_url.get_secret_value()
    if not dsn:
        raise RuntimeError("Real PostgreSQL test requires DATABASE_URL")
    name = "dr4a_test_" + uuid4().hex
    if not re.fullmatch(r"dr4a_test_[a-f0-9]{32}", name):
        raise RuntimeError("Unsafe test database identity")
    admin = await asyncpg.connect(dsn, database="postgres", timeout=10)
    pool = None
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        created = True
        print("isolated_pg_database=" + name)
        pool = await asyncpg.create_pool(dsn, database=name, min_size=1, max_size=6, timeout=10)
        yield pool, name
    finally:
        if pool is not None:
            try:
                await asyncio.wait_for(pool.close(), timeout=5)
            except TimeoutError:
                pool.terminate()
        if created:
            # Exact generated name, not a DSN's configured database or a wildcard.
            await admin.execute(f'DROP DATABASE "{name}"')
            print("removed_isolated_pg_database=" + name)
        await admin.close()
