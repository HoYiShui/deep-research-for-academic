"""Real PG lifecycle fencing, not external partition or object cleanup."""

import asyncio
import json
import os
import sys
from uuid import uuid4

import pytest

from application.errors import AppError
from tests.integration.test_mono_kb_lifecycle import seeded, submit
from tests.integration.test_mono_knowledge_management import deleting
from tests.knowledge_fixtures import submission


async def claim(store, owner, kb, worker="worker", document=None, **kwargs):
    async with store.transaction() as tx:
        return await store.knowledge.claim_lifecycle(
            owner,
            kb.kb_id,
            worker,
            tx,
            document_id=document.document_id if document else None,
            **kwargs,
        )


async def expired(pool, kb, document=None):
    table, identity, value = (
        ("documents", "document_id", document.document_id)
        if document
        else ("knowledge_bases", "kb_id", kb.kb_id)
    )
    await pool.execute(
        f"UPDATE {table} SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE {identity}=$1",
        value,
    )


async def cursor(store, owner, kb, held, value, document=None):
    async with store.transaction() as tx:
        return await store.knowledge.commit_cleanup_cursor(
            owner,
            kb.kb_id,
            held.lease_token,
            held.revision,
            value,
            tx,
            document_id=document.document_id if document else None,
        )


@pytest.mark.parametrize("kind", ["kb", "document"])
async def test_claim_competition_reentry_expiry_and_old_token_rejection(pg_database, kind):
    pool, store, owner, kb = await seeded(pg_database)
    document = None
    if kind == "document":
        values = submission(kb)
        await submit(store, owner, values)
        document = values[0]
    await deleting(store, owner, kb, document)
    results = await asyncio.gather(
        claim(store, owner, kb, "A", document),
        claim(store, owner, kb, "B", document),
        return_exceptions=True,
    )
    winners = [item for item in results if not isinstance(item, Exception)]
    assert len(winners) == 1
    assert sum(isinstance(item, AppError) and item.code == "document_busy" for item in results) == 1
    held = winners[0]
    assert await claim(store, owner, kb, held.lease_owner, document) == held
    await expired(pool, kb, document)
    replacement = await claim(store, owner, kb, "new", document)
    assert replacement.lease_token == held.lease_token + 1
    with pytest.raises(AppError, match="stale_resource"):
        await cursor(store, owner, kb, held, "index", document)
    committed = await cursor(store, owner, kb, replacement, "index", document)
    assert committed.cleanup_cursor == "index" and committed.revision == replacement.revision + 1


@pytest.mark.parametrize("kind", ["kb", "document"])
async def test_cursor_steps_cas_and_release_keep_recovery_position(pg_database, kind):
    _, store, owner, kb = await seeded(pg_database)
    document = None
    if kind == "document":
        values = submission(kb)
        await submit(store, owner, values)
        document = values[0]
    await deleting(store, owner, kb, document)
    held = await claim(store, owner, kb, document=document)
    with pytest.raises(AppError, match="invalid_state"):
        await cursor(store, owner, kb, held, "metadata", document)
    current = await cursor(store, owner, kb, held, "index", document)
    assert await cursor(store, owner, kb, current, "index", document) == current
    with pytest.raises(AppError, match="stale_resource"):
        await cursor(store, owner, kb, held, "index", document)
    with pytest.raises(AppError, match="invalid_state"):
        await cursor(store, owner, kb, current, "wait_jobs", document)
    for value in ["objects", "metadata"]:
        current = await cursor(store, owner, kb, current, value, document)
    async with store.transaction() as tx:
        released = await store.knowledge.release_lifecycle(
            owner,
            kb.kb_id,
            "worker",
            current.lease_token,
            tx,
            document_id=document.document_id if document else None,
        )
    assert released.cleanup_cursor == "metadata" and released.lease_owner is None
    assert released.revision == current.revision
    replacement = await claim(store, owner, kb, "restarted", document)
    assert (
        replacement.cleanup_cursor == "metadata"
        and replacement.lease_token == current.lease_token + 1
    )


async def test_creation_cannot_revive_deleting_and_uses_same_lease(pg_database):
    pool, store, owner, kb = await seeded(pg_database, status="creating")
    held = await claim(store, owner, kb)
    with pytest.raises(AppError, match="service_not_ready"):
        async with store.transaction() as tx:
            await store.knowledge.finish_kb_creation(
                owner, kb.kb_id, held.lease_token, held.revision, tx
            )
    barrier = await deleting(store, owner, kb)
    assert barrier.lease_token == held.lease_token and barrier.lease_owner == held.lease_owner
    with pytest.raises(AppError, match="document_busy"):
        await claim(store, owner, kb, "cleaner")
    with pytest.raises(AppError, match="resource_not_active"):
        async with store.transaction() as tx:
            await store.knowledge.finish_kb_creation(
                owner, kb.kb_id, held.lease_token, held.revision, tx, partition_verified=True
            )
    await expired(pool, kb)
    cleaning = await claim(store, owner, kb, "cleaner")
    assert cleaning.status == "deleting" and cleaning.lease_token > held.lease_token


async def test_creation_success_requires_live_fence_and_verified_signal(pg_database):
    pool, store, owner, kb = await seeded(pg_database, status="creating")
    held = await claim(store, owner, kb)
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            await store.knowledge.finish_kb_creation(
                owner, kb.kb_id, held.lease_token + 1, held.revision, tx, partition_verified=True
            )
    async with store.transaction() as tx:
        active = await store.knowledge.finish_kb_creation(
            owner, kb.kb_id, held.lease_token, held.revision, tx, partition_verified=True
        )
    assert active.status == "active" and active.revision == 2 and active.lease_owner is None
    assert await pool.fetchval("SELECT status FROM knowledge_bases") == "active"


async def test_heartbeat_worker_expiry_and_sql_clock_fence(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    await deleting(store, owner, kb)
    held = await claim(store, owner, kb, lease_s=1)
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            await store.knowledge.renew_lifecycle(owner, kb.kb_id, "wrong", held.lease_token, tx)
    async with store.transaction() as tx:
        renewed = await store.knowledge.renew_lifecycle(
            owner, kb.kb_id, "worker", held.lease_token, tx, lease_s=1
        )
    assert renewed.lease_expires_at >= held.lease_expires_at
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            # Actual passage of time inside a held PG transaction, not a fake clock.
            await store.knowledge.get_kb(owner, kb.kb_id, tx, for_update=True)
            await store.connection(tx).execute("SELECT pg_sleep(1.1)")
            await store.knowledge.commit_cleanup_cursor(
                owner, kb.kb_id, held.lease_token, held.revision, "index", tx
            )
    for operation in ["renew_lifecycle", "release_lifecycle"]:
        with pytest.raises(AppError, match="stale_resource"):
            async with store.transaction() as tx:
                await getattr(store.knowledge, operation)(
                    owner, kb.kb_id, "worker", held.lease_token, tx
                )
    assert (await store.knowledge.get_kb(owner, kb.kb_id)).cleanup_cursor == "wait_jobs"


async def test_owner_parent_and_scan_inventory(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    await submit(store, owner, values)
    await deleting(store, owner, kb, values[0])
    await deleting(store, owner, kb)
    with pytest.raises(AppError, match="knowledge_base_not_found"):
        await claim(store, uuid4(), kb)
    with pytest.raises(AppError, match="document_not_found"):
        async with store.transaction() as tx:
            await store.knowledge.claim_lifecycle(
                owner, kb.kb_id, "worker", tx, document_id=uuid4()
            )
    async with store.transaction() as tx:
        inventory = await store.knowledge.scan_lifecycle(tx)
    assert {(item["kind"], item["owner_id"]) for item in inventory} == {
        ("kb", owner),
        ("document", owner),
    }
    await claim(store, owner, kb)
    async with store.transaction() as tx:
        remaining = await store.knowledge.scan_lifecycle(tx)
    assert len(remaining) == 1 and remaining[0]["document_id"] == values[0].document_id


@pytest.mark.parametrize(
    "worker,lease_s", [("", 30), (" worker ", 30), ("w", True), ("w", 0), ("w", 301)]
)
async def test_bad_lease_parameters_rejected(pg_database, worker, lease_s):
    _, store, owner, kb = await seeded(pg_database, status="creating")
    with pytest.raises(ValueError):
        await claim(store, owner, kb, worker, lease_s=lease_s)


@pytest.mark.skipif(os.name != "posix", reason="Requires actual POSIX SIGKILL")
async def test_sigkill_worker_preserves_cursor_and_fences_recovery(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    await deleting(store, owner, kb)
    # The child commits its own real PG lease and cursor, then remains alive.
    program = """
import asyncio, json, sys
from uuid import UUID
import asyncpg
from application.settings import Settings
from infrastructure.storage.research_postgres import PostgresResearchStore
async def main():
    config = json.loads(sys.stdin.readline())
    pool = await asyncpg.create_pool(Settings.load().database_url.get_secret_value(),
        database=config['database'], min_size=1, max_size=1)
    store = PostgresResearchStore(pool)
    owner, kb = UUID(config['owner']), UUID(config['kb'])
    async with store.transaction() as tx:
        held = await store.knowledge.claim_lifecycle(owner, kb, 'kill-test', tx, lease_s=1)
    async with store.transaction() as tx:
        current = await store.knowledge.commit_cleanup_cursor(owner, kb, held.lease_token,
            held.revision, 'index', tx)
    print(json.dumps({'token': current.lease_token, 'revision': current.revision}), flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        program,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        process.stdin.write(
            (
                json.dumps({"database": pg_database[1], "owner": str(owner), "kb": str(kb.kb_id)})
                + "\n"
            ).encode()
        )
        await process.stdin.drain()
        process.stdin.close()
        committed = json.loads(await asyncio.wait_for(process.stdout.readline(), timeout=15))
        assert process.returncode is None
        process.kill()  # actual SIGKILL; no cleanup/finally in child
        await asyncio.wait_for(process.wait(), timeout=5)
        assert process.returncode == -9
        await asyncio.sleep(1.1)
        async with store.transaction() as tx:
            inventory = await store.knowledge.scan_lifecycle(tx)
        assert len(inventory) == 1 and inventory[0]["kb_id"] == kb.kb_id
        recovered = await claim(store, owner, kb, "recovery")
        assert recovered.cleanup_cursor == "index"
        assert recovered.revision == committed["revision"]
        assert recovered.lease_token > committed["token"]
        with pytest.raises(AppError, match="stale_resource"):
            async with store.transaction() as tx:
                await store.knowledge.commit_cleanup_cursor(
                    owner, kb.kb_id, committed["token"], committed["revision"], "objects", tx
                )
        continued = await cursor(store, owner, kb, recovered, "objects")
        assert continued.cleanup_cursor == "objects" and continued.status == "deleting"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
