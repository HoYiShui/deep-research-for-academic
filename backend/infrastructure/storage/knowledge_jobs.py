"""PG control primitives; Services must verify external source/cleanup results."""

from datetime import timedelta

from application.errors import AppError
from application.knowledge_models import IngestionJob, IngestionProgress, JobAttempt

STEPS = ("upload", "parse", "chunk", "embed", "index", "commit", "cleanup")


class KnowledgeJobs:
    async def recover_expired_jobs(self, tx, *, limit=100):
        """Trusted scanner records interruptions; it never silently retries work."""
        from domain.research.models import Failure

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid recovery scan limit")
        conn = self.store.connection(tx)
        candidates = await conn.fetch(
            "SELECT j.job_id,k.owner_id FROM ingestion_jobs j JOIN knowledge_bases k USING(kb_id) "
            "WHERE j.status='processing' AND j.lease_expires_at<=clock_timestamp() "
            "ORDER BY j.lease_expires_at,j.job_id LIMIT $1",
            limit,
        )
        recovered = []
        for candidate in candidates:
            _, kb, document, job, now = await self._lock_job(
                candidate["owner_id"], candidate["job_id"], tx, require_active=False
            )
            if job.status != "processing" or job.lease_expires_at > now:
                continue
            deleting = kb.status != "active" or document.status != "active"
            recoverable = not deleting and job.attempt_count < 3
            failure = Failure(
                code="interrupted",
                dependency="worker",
                operation="ingest",
                phase=None,
                message="Ingestion worker lease expired",
                retryable=recoverable,
                resume_allowed=recoverable,
                attempt=job.attempt_count,
                occurred_at=now,
                details=None,
            )
            attempt = JobAttempt.model_validate(
                job.attempt_history[-1].model_dump()
                | {
                    "status": "cancelled" if deleting else "failed",
                    "finished_at": now,
                    "failure": failure,
                    "progress": job.progress,
                }
            )
            updated = IngestionJob.model_validate(
                job.model_dump()
                | {
                    "status": "cancelling" if deleting else "failed",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "lease_token": job.lease_token + 1,
                    "finished_at": None if deleting else now,
                    "failure": failure,
                    "cancel_requested_at": now if deleting else job.cancel_requested_at,
                    "attempt_history": [*job.attempt_history[:-1], attempt],
                    "progress": IngestionProgress(
                        step="cleanup",
                        completed_units=0,
                        total_units=None,
                        completed_batches=job.progress.completed_batches,
                    )
                    if deleting
                    else job.progress,
                }
            )
            await conn.execute(
                "UPDATE document_versions SET status='failed' WHERE document_version_id=$1 AND status='staging'",
                job.document_version_id,
            )
            await self._save_job(conn, updated)
            recovered.append(updated)
        return recovered

    @staticmethod
    def _cleanup_fence(job, token, now):
        if (
            type(token) is not int
            or job.status != "cancelling"
            or job.lease_owner is None
            or job.lease_token != token
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise AppError("stale_resource", "Cleanup lease is not current")

    async def heartbeat_job(self, owner, job_id, token, worker, tx, *, lease_s=30):
        if type(lease_s) is not int or not 1 <= lease_s <= 300:
            raise ValueError("Invalid heartbeat lease duration")
        conn, kb, document, job, now = await self._lock_job(owner, job_id, tx, require_active=False)
        if job.lease_owner != worker:
            raise AppError("stale_resource", "Worker does not own this lease")
        if job.status == "cancelling":
            self._cleanup_fence(job, token, now)
        else:
            if kb.status != "active" or document.status != "active":
                raise AppError("resource_not_active", "Ingestion resource is not active")
            self._fence(job, token, now)
        renewed = IngestionJob.model_validate(
            job.model_dump()
            | {
                "lease_expires_at": max(job.lease_expires_at, now + timedelta(seconds=lease_s)),
            }
        )
        await self._save_job(conn, renewed, expected_token=token, expected_status=job.status)
        return renewed

    async def commit_job_progress(self, owner, job_id, token, progress, tx):
        progress = IngestionProgress.model_validate(progress)
        conn, kb, document, job, now = await self._lock_job(owner, job_id, tx, require_active=False)
        if job.status == "cancelling":
            self._cleanup_fence(job, token, now)
            if progress.step != "cleanup":
                raise AppError("stale_resource", "Cancelled job cannot publish ingestion progress")
        else:
            if kb.status != "active" or document.status != "active":
                raise AppError("resource_not_active", "Ingestion resource is not active")
            self._fence(job, token, now)
            if progress.step == "cleanup":
                raise AppError("invalid_state", "Processing job is not cleaning up")
        previous = job.progress
        if (
            STEPS.index(progress.step) < STEPS.index(previous.step)
            or not set(previous.completed_batches) <= set(progress.completed_batches)
            or (
                progress.step == previous.step
                and (
                    progress.completed_units < previous.completed_units
                    or (
                        previous.total_units is not None
                        and progress.total_units != previous.total_units
                    )
                )
            )
        ):
            raise AppError("invalid_state", "Progress cannot regress or lose committed batches")
        history = list(job.attempt_history)
        if job.status == "processing":
            history[-1] = JobAttempt.model_validate(
                history[-1].model_dump() | {"progress": progress}
            )
        updated = IngestionJob.model_validate(
            job.model_dump() | {"progress": progress, "attempt_history": history}
        )
        await self._save_job(conn, updated, expected_token=token, expected_status=job.status)
        return updated

    async def request_job_cancel(self, owner, job_id, tx):
        conn, _, _, job, now = await self._lock_job(owner, job_id, tx, require_active=False)
        if job.status == "completed":
            raise AppError("invalid_session_state", "Completed ingestion cannot be cancelled")
        if job.status in {"cancelling", "cancelled"}:
            return job
        pending = IngestionJob.model_validate(
            job.model_dump()
            | {
                "status": "cancelling",
                "cancel_requested_at": now,
                "finished_at": None,
                "progress": IngestionProgress(
                    step="cleanup",
                    completed_units=0,
                    total_units=None,
                    completed_batches=job.progress.completed_batches,
                ),
            }
        )
        await self._save_job(conn, pending)
        return pending

    async def claim_job_cleanup(self, owner, job_id, worker, tx, *, lease_s=30):
        if (
            not isinstance(worker, str)
            or not worker.strip()
            or type(lease_s) is not int
            or not 1 <= lease_s <= 300
        ):
            raise ValueError("Invalid cleanup lease")
        conn, _, _, job, now = await self._lock_job(owner, job_id, tx, require_active=False)
        if job.status != "cancelling":
            raise AppError("invalid_session_state", "Job is not awaiting cleanup")
        if job.lease_expires_at is not None and job.lease_expires_at > now:
            if job.lease_owner == worker:
                return job
            raise AppError("document_busy", "Current worker still holds cleanup ownership")
        claimed = IngestionJob.model_validate(
            job.model_dump()
            | {
                "lease_owner": worker,
                "lease_token": job.lease_token + 1,
                "lease_expires_at": now + timedelta(seconds=lease_s),
            }
        )
        await self._save_job(conn, claimed)
        return claimed

    async def finish_job_cancel(self, owner, job_id, token, tx, *, cleanup_verified=False):
        conn, _, _, job, now = await self._lock_job(owner, job_id, tx, require_active=False)
        self._cleanup_fence(job, token, now)
        if cleanup_verified is not True:
            raise AppError(
                "content_unavailable", "External staging cleanup is not verified", retryable=True
            )
        version_status = await conn.fetchval(
            "SELECT status FROM document_versions WHERE document_version_id=$1",
            job.document_version_id,
        )
        if version_status not in {"staging", "failed"}:
            raise AppError("invalid_state", "Published version cannot be cancelled")
        await conn.execute(
            "DELETE FROM chunks WHERE document_version_id=$1", job.document_version_id
        )
        await conn.execute(
            "UPDATE document_versions SET status='failed',parsed_object_key=NULL,manifest_object_key=NULL,chunk_count=0 WHERE document_version_id=$1",
            job.document_version_id,
        )
        history = list(job.attempt_history)
        if history and history[-1].status == "processing":
            history[-1] = JobAttempt.model_validate(
                history[-1].model_dump() | {"status": "cancelled", "finished_at": now}
            )
        cancelled = IngestionJob.model_validate(
            job.model_dump()
            | {
                "status": "cancelled",
                "lease_owner": None,
                "lease_expires_at": None,
                "finished_at": now,
                "attempt_history": history,
                "progress": job.progress.model_dump() | {"completed_batches": []},
            }
        )
        await self._save_job(conn, cancelled, expected_token=token, expected_status="cancelling")
        return cancelled

    async def retry_job(self, owner, job_id, tx, *, source_verified=False):
        conn, _, _, job, _ = await self._lock_job(owner, job_id, tx)
        if (
            job.status not in {"failed", "cancelled"}
            or job.attempt_count >= 3
            or (
                job.status == "failed"
                and (
                    job.failure is None
                    or not job.failure.retryable
                    or not job.failure.resume_allowed
                )
            )
            or source_verified is not True
        ):
            raise AppError("resume_not_allowed", "Job cannot be retried with this source/state")
        changed = await conn.fetchval(
            "UPDATE document_versions SET status='staging' WHERE document_version_id=$1 AND status='failed' RETURNING document_version_id",
            job.document_version_id,
        )
        if changed is None:
            raise AppError("invalid_state", "Failed version is missing")
        accepted = IngestionJob.model_validate(
            job.model_dump()
            | {
                "status": "accepted",
                "failure": None,
                "finished_at": None,
                "cancel_requested_at": None,
                "progress": IngestionProgress(
                    step="upload",
                    completed_units=0,
                    total_units=None,
                    completed_batches=job.progress.completed_batches,
                ),
            }
        )
        await self._save_job(conn, accepted)
        return accepted
