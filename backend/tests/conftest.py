"""Explicit real PostgreSQL fixtures; never reset the configured database."""

import asyncio
import re
from uuid import uuid4

import asyncpg
import pytest_asyncio
from minio import Minio

from application.settings import Settings
from infrastructure.storage.content_cache import MinioResultCache


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


@pytest_asyncio.fixture
async def object_cache():
    """Real MinIO objects only in a unique invocation-owned disposable bucket."""
    settings = Settings.load()
    bucket = "dr4a-test-" + uuid4().hex
    access = settings.minio_access_key.get_secret_value()
    secret = settings.minio_secret_key.get_secret_value()
    admin = Minio(
        settings.minio_endpoint, access_key=access, secret_key=secret, secure=settings.minio_secure
    )
    cache = MinioResultCache(
        settings.minio_endpoint, access, secret, bucket, secure=settings.minio_secure
    )
    created = False
    try:
        await asyncio.to_thread(admin.make_bucket, bucket)
        created = True
        print("isolated_minio_bucket=" + bucket)
        yield cache
    finally:
        await cache.close()
        if created:

            def cleanup():
                for item in admin.list_objects(bucket, recursive=True):
                    admin.remove_object(bucket, item.object_name)
                admin.remove_bucket(bucket)

            await asyncio.to_thread(cleanup)
            print("removed_isolated_minio_bucket=" + bucket)
