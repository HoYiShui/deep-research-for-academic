"""Real PG job-control contracts; no simulated external cleanup acceptance."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from application.errors import AppError
from application.knowledge_models import IngestionProgress
from domain.research.models import Failure
from tests.integration.test_mono_kb_lifecycle import activate, seeded, submit
from tests.knowledge_fixtures import submission


async def held_job(pg_database):
    pool, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    await submit(store, owner, values)
    async with store.transaction() as tx:
        held = await store.knowledge.claim_job(owner, values[2].job_id, "fixture", tx)
    return pool, store, owner, values, held


async def test_heartbeat_and_progress_preserve_token_and_attempt(pg_database):
    _, store, owner, _, held = await held_job(pg_database)
    async with store.transaction() as tx:
        renewed = await store.knowledge.heartbeat_job(
            owner, held.job_id, held.lease_token, "fixture", tx, lease_s=60
        )
    assert renewed.lease_expires_at > held.lease_expires_at
    assert renewed.lease_token == held.lease_token and renewed.attempt_count == 1
    progress = IngestionProgress(
        step="embed", completed_units=1, total_units=2, completed_batches=["embed-0"]
    )
    async with store.transaction() as tx:
        saved = await store.knowledge.commit_job_progress(
            owner, held.job_id, held.lease_token, progress, tx
        )
    assert saved.progress == progress and saved.attempt_history[-1].progress == progress
    async with store.transaction() as tx:
        duplicate = await store.knowledge.commit_job_progress(
            owner, held.job_id, held.lease_token, progress, tx
        )
    assert duplicate == saved
    for bad in (
        progress.model_dump() | {"completed_units": 0},
        progress.model_dump() | {"completed_batches": []},
        progress.model_dump() | {"step": "parse"},
    ):
        with pytest.raises(AppError, match="invalid_state"):
            async with store.transaction() as tx:
                await store.knowledge.commit_job_progress(
                    owner, held.job_id, held.lease_token, IngestionProgress.model_validate(bad), tx
                )


@pytest.mark.parametrize("fault", ["owner", "worker", "token", "expired"])
async def test_heartbeat_never_revives_or_borrows_a_lease(pg_database, fault):
    pool, store, owner, _, held = await held_job(pg_database)
    worker, token = "fixture", held.lease_token
    if fault == "owner":
        owner = uuid4()
    elif fault == "worker":
        worker = "not-owner"
    elif fault == "token":
        token += 1
    else:
        await pool.execute(
            "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'"
        )
    with pytest.raises(AppError, match="job_not_found|stale_resource"):
        async with store.transaction() as tx:
            await store.knowledge.heartbeat_job(owner, held.job_id, token, worker, tx)


async def test_cancel_preserves_worker_until_cleanup_then_retry_keeps_identity(pg_database):
    pool, store, owner, values, held = await held_job(pg_database)
    async with store.transaction() as tx:
        pending = await store.knowledge.request_job_cancel(owner, held.job_id, tx)
    assert pending.status == "cancelling" and pending.lease_token == held.lease_token
    assert pending.lease_owner == "fixture" and pending.finished_at is None
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            await store.knowledge.commit_job_progress(
                owner,
                held.job_id,
                held.lease_token,
                IngestionProgress(
                    step="index", completed_units=0, total_units=None, completed_batches=[]
                ),
                tx,
            )
    with pytest.raises(AppError, match="document_busy"):
        async with store.transaction() as tx:
            await store.knowledge.claim_job_cleanup(owner, held.job_id, "other-worker", tx)
    with pytest.raises(AppError, match="content_unavailable"):
        async with store.transaction() as tx:
            await store.knowledge.finish_job_cancel(
                owner, held.job_id, held.lease_token, tx, cleanup_verified=False
            )
    assert (await store.knowledge.get_job(owner, held.job_id)).status == "cancelling"
    # PG-only fixture explicitly signals cleanup. Actual MinIO/Milvus proof belongs to the service tests.
    async with store.transaction() as tx:
        cancelled = await store.knowledge.finish_job_cancel(
            owner, held.job_id, held.lease_token, tx, cleanup_verified=True
        )
    assert cancelled.status == "cancelled" and cancelled.lease_owner is None
    assert cancelled.attempt_history[-1].status == "cancelled"
    assert await pool.fetchval("SELECT status FROM document_versions") == "failed"
    with pytest.raises(AppError, match="resume_not_allowed"):
        async with store.transaction() as tx:
            await store.knowledge.retry_job(owner, held.job_id, tx, source_verified=False)
    async with store.transaction() as tx:
        retried = await store.knowledge.retry_job(owner, held.job_id, tx, source_verified=True)
        again = await store.knowledge.claim_job(owner, retried.job_id, "new-worker", tx)
    assert (
        again.job_id == held.job_id and again.document_version_id == values[1].document_version_id
    )
    assert again.attempt_count == 2 and again.lease_token > held.lease_token
    assert again.attempt_history[0] == cancelled.attempt_history[0]
    assert again.cancel_requested_at is None
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            await store.knowledge.heartbeat_job(owner, held.job_id, held.lease_token, "fixture", tx)


async def test_accepted_cancel_requires_cleanup_lease_and_deleted_resource_can_be_cleaned(
    pg_database,
):
    pool, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    await submit(store, owner, values)
    job_id = values[2].job_id
    async with store.transaction() as tx:
        await store.knowledge.request_job_cancel(owner, job_id, tx)
    await pool.execute("UPDATE knowledge_bases SET status='deleting'")
    async with store.transaction() as tx:
        cleanup = await store.knowledge.claim_job_cleanup(owner, job_id, "cleanup-worker", tx)
        done = await store.knowledge.finish_job_cancel(
            owner, job_id, cleanup.lease_token, tx, cleanup_verified=True
        )
    assert done.status == "cancelled" and done.attempt_count == 0 and done.attempt_history == []
    with pytest.raises(AppError, match="resource_not_active"):
        async with store.transaction() as tx:
            await store.knowledge.retry_job(owner, job_id, tx, source_verified=True)


async def test_expired_worker_cleanup_takeover_fences_old_worker(pg_database):
    pool, store, owner, _, held = await held_job(pg_database)
    async with store.transaction() as tx:
        await store.knowledge.request_job_cancel(owner, held.job_id, tx)
    await pool.execute(
        "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        cleanup = await store.knowledge.claim_job_cleanup(owner, held.job_id, "cleanup-worker", tx)
    assert cleanup.lease_token > held.lease_token
    with pytest.raises(AppError, match="stale_resource"):
        async with store.transaction() as tx:
            await store.knowledge.finish_job_cancel(
                owner, held.job_id, held.lease_token, tx, cleanup_verified=True
            )
    async with store.transaction() as tx:
        assert (
            await store.knowledge.finish_job_cancel(
                owner, held.job_id, cleanup.lease_token, tx, cleanup_verified=True
            )
        ).status == "cancelled"


async def test_retry_preserves_failed_history_and_enforces_attempt_limit(pg_database):
    _, store, owner, _, held = await held_job(pg_database)
    for attempt in range(1, 4):
        failure = Failure(
            code="parser_failed",
            dependency="parser",
            operation="parse",
            phase=None,
            message="Controlled failure",
            retryable=True,
            resume_allowed=True,
            attempt=attempt,
            occurred_at=datetime.now(UTC),
            details=None,
        )
        async with store.transaction() as tx:
            failed = await store.knowledge.fail_job(
                owner, held.job_id, held.lease_token, failure, tx
            )
        if attempt < 3:
            async with store.transaction() as tx:
                await store.knowledge.retry_job(owner, held.job_id, tx, source_verified=True)
                held = await store.knowledge.claim_job(owner, held.job_id, "fixture", tx)
            assert held.attempt_count == attempt + 1
            assert held.attempt_history[:-1] == failed.attempt_history
    with pytest.raises(AppError, match="resume_not_allowed"):
        async with store.transaction() as tx:
            await store.knowledge.retry_job(owner, held.job_id, tx, source_verified=True)


async def test_expired_processing_records_interruption_once_without_auto_retry(pg_database):
    pool, store, owner, _, held = await held_job(pg_database)
    await pool.execute(
        "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        recovered = await store.knowledge.recover_expired_jobs(tx)
    assert len(recovered) == 1
    failed = await store.knowledge.get_job(owner, held.job_id)
    assert failed.status == "failed" and failed.lease_owner is None
    assert failed.failure.code == "interrupted" and failed.failure.resume_allowed
    assert failed.attempt_count == 1 and failed.attempt_history[-1].status == "failed"
    assert failed.lease_token > held.lease_token
    async with store.transaction() as tx:
        assert await store.knowledge.recover_expired_jobs(tx) == []
    assert await pool.fetchval("SELECT status FROM document_versions") == "failed"


async def test_expired_processing_under_delete_barrier_waits_for_cleanup(pg_database):
    pool, store, owner, _, held = await held_job(pg_database)
    await pool.execute("UPDATE knowledge_bases SET status='deleting'")
    await pool.execute(
        "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )
    async with store.transaction() as tx:
        recovered = await store.knowledge.recover_expired_jobs(tx)
    assert len(recovered) == 1 and recovered[0].status == "cancelling"
    assert recovered[0].lease_owner is None and recovered[0].finished_at is None
    async with store.transaction() as tx:
        cleanup = await store.knowledge.claim_job_cleanup(owner, held.job_id, "cleaner", tx)
        done = await store.knowledge.finish_job_cancel(
            owner, held.job_id, cleanup.lease_token, tx, cleanup_verified=True
        )
    assert done.status == "cancelled"


async def test_completed_ingestion_cannot_be_cancelled_or_retried(pg_database):
    _, store, owner, kb = await seeded(pg_database)
    values = submission(kb)
    await submit(store, owner, values)
    await activate(store, owner, values)
    with pytest.raises(AppError, match="invalid_session_state"):
        async with store.transaction() as tx:
            await store.knowledge.request_job_cancel(owner, values[2].job_id, tx)
    with pytest.raises(AppError, match="resume_not_allowed"):
        async with store.transaction() as tx:
            await store.knowledge.retry_job(owner, values[2].job_id, tx, source_verified=True)


async def test_failed_job_cancellation_does_not_skip_cleanup_and_preserves_failure_history(
    pg_database,
):
    _, store, owner, _, held = await held_job(pg_database)
    failure = Failure(
        code="invalid_source",
        dependency="parser",
        operation="parse",
        phase=None,
        message="Controlled nonrecoverable failure",
        retryable=False,
        resume_allowed=False,
        attempt=1,
        occurred_at=datetime.now(UTC),
        details=None,
    )
    async with store.transaction() as tx:
        failed = await store.knowledge.fail_job(owner, held.job_id, held.lease_token, failure, tx)
    with pytest.raises(AppError, match="resume_not_allowed"):
        async with store.transaction() as tx:
            await store.knowledge.retry_job(owner, held.job_id, tx, source_verified=True)
    async with store.transaction() as tx:
        pending = await store.knowledge.request_job_cancel(owner, held.job_id, tx)
        assert pending.status == "cancelling"
        assert await store.knowledge.request_job_cancel(owner, held.job_id, tx) == pending
        cleanup = await store.knowledge.claim_job_cleanup(owner, held.job_id, "cleaner", tx)
        cancelled = await store.knowledge.finish_job_cancel(
            owner, held.job_id, cleanup.lease_token, tx, cleanup_verified=True
        )
        assert await store.knowledge.request_job_cancel(owner, held.job_id, tx) == cancelled
    assert cancelled.status == "cancelled" and cancelled.attempt_history == failed.attempt_history


async def test_concurrent_recovery_records_one_failed_attempt(pg_database):
    import asyncio

    pool, store, owner, _, held = await held_job(pg_database)
    await pool.execute(
        "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'"
    )

    async def recover():
        async with store.transaction() as tx:
            return await store.knowledge.recover_expired_jobs(tx)

    results = await asyncio.gather(recover(), recover())
    assert sorted(len(result) for result in results) == [0, 1]
    failed = await store.knowledge.get_job(owner, held.job_id)
    assert failed.attempt_count == 1 and len(failed.attempt_history) == 1
