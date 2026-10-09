"""Read-only, payload-free diagnostics do not swallow a real integration failure."""

import json

import pytest

from tests.integration.test_mono_run_lifecycle import ready
from tests.integration.test_mono_transactions import setup_store
from tests.support.run_diagnostics import diagnose_on_failure, snapshot


async def test_metadata_snapshot_has_persisted_state_not_private_payloads(pg_database):
    pool, store, owner = await setup_store(pg_database)
    commit = await ready(store, owner.user_id)
    canary = "private-diagnostics-canary"
    await pool.execute("UPDATE sessions SET query=$1", canary)
    before = await pool.fetchval("SELECT count(*) FROM phase_snapshots")
    value = await snapshot(pool)
    assert value["runs"][0]["run_id"] == commit.run.run_id
    assert value["runs"][0]["checkpoint_seq"] == 1
    assert value["runs"][0]["status"] == "ready"
    assert value["checkpoints"][0]["seq"] == 1
    serialized = json.dumps(value, default=str)
    for forbidden in (
        canary,
        "config_snapshot",
        "result_object_key",
        "research_brief",
        "request_hash",
    ):
        assert forbidden not in serialized
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots") == before


async def test_diagnostic_context_preserves_original_failure(pg_database, capsys):
    pool, _, _ = await setup_store(pg_database)
    failure = RuntimeError("controlled original failure")
    with pytest.raises(RuntimeError) as caught:
        async with diagnose_on_failure(pool):
            raise failure
    assert caught.value is failure
    assert "run_failure_metadata=" in capsys.readouterr().out


async def test_diagnostics_refuse_non_test_database_and_sanitize_dependency_failure():
    class NotIsolated:
        async def fetchval(self, query):
            return "deepresearch"

        async def fetch(self, query):
            raise AssertionError("Must not read a user's database")

    assert await snapshot(NotIsolated()) == {"unavailable": "not_an_isolated_test_database"}

    class Offline:
        async def fetchval(self, query):
            raise RuntimeError("postgres://secret-password@private-host")

    assert await snapshot(Offline()) == {"unavailable": "RuntimeError"}
