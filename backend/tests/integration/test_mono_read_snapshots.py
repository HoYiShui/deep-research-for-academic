"""Real PG read projections must stay coherent without competing with Run claims."""

import asyncio

import pytest

from application.research_queries import ResearchQueries
from tests.integration.test_mono_run_lifecycle import ready
from tests.integration.test_mono_transactions import setup_store


@pytest.mark.parametrize("method", ["session_view", "checkpoint_view"])
async def test_projection_does_not_skip_ready_claim_and_keeps_one_snapshot(
    pg_database, monkeypatch, method
):
    _, store, owner = await setup_store(pg_database)
    commit = await ready(store, owner.user_id)
    queries = ResearchQueries(store, store.research)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.research.get_run

    async def paused(*args, **kwargs):
        # Session has been read, but the Run has not. A concurrent transition
        # must not be blocked, and must not turn this read into a mixed state.
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(store.research, "get_run", paused)
    reading = asyncio.create_task(
        getattr(queries, method)(owner.user_id, commit.session.session_id)
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        async with store.transaction() as tx:
            claimed = await store.research.claim_run("claim-during-read", tx)
        assert claimed is not None, "A GET projection must not make a ready Run invisible to claim"
        release.set()
        old = await asyncio.wait_for(reading, 2)
        if method == "session_view":
            assert old["status"] == "ready" and old["revision"] == commit.session.revision
        else:
            assert old["checkpoint_seq"] == 1
        current = await queries.session_view(owner.user_id, commit.session.session_id)
        assert current["status"] == "running" and current["revision"] == commit.session.revision + 1
    finally:
        release.set()
        await asyncio.gather(reading, return_exceptions=True)


async def test_snapshot_has_db_enforced_repeatable_read_and_write_rejection(pg_database):
    _, store, owner = await setup_store(pg_database)
    commit = await ready(store, owner.user_id)
    async with store.snapshot() as tx:
        conn = store.connection(tx)
        assert await conn.fetchval("SHOW transaction_isolation") == "repeatable read"
        assert await conn.fetchval("SHOW transaction_read_only") == "on"
        before = await store.research.get_session(owner.user_id, commit.session.session_id, tx)
        async with store.transaction() as mutation:
            assert await store.research.claim_run("concurrent", mutation)
        after = await store.research.get_session(owner.user_id, commit.session.session_id, tx)
        assert before == after and after.status == "ready"
    from domain.ports import AdapterError

    with pytest.raises(AdapterError):
        async with store.snapshot() as tx:
            await store.connection(tx).execute(
                "UPDATE sessions SET revision=revision+1 WHERE session_id=$1",
                commit.session.session_id,
            )
    assert (
        await store.research.get_session(owner.user_id, commit.session.session_id)
    ).revision == before.revision + 1
