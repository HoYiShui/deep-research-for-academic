"""Fenced deletion metadata. External I/O is never performed in these transactions."""

from application.errors import AppError
from application.knowledge_models import (
    CleanupCandidate,
    DeletionContext,
    DocumentVersion,
    IngestionJob,
    JobAttempt,
)
from domain.research.models import Failure


class KnowledgeDeletion:
    async def scan_deletion(self, tx, *, tombstone=False, interval_s=60, limit=1):
        if type(tombstone) is not bool or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid cleanup inventory")
        if type(interval_s) is not int or not 1 <= interval_s <= 86400:
            raise ValueError("Invalid tombstone scan interval")
        status = "deleted" if tombstone else "deleting"
        rows = await self.store.connection(tx).fetch(
            "SELECT k.owner_id,k.kb_id,NULL::uuid AS document_id,"
            "COALESCE(k.cleanup_verified_at,k.updated_at) AS scanned_at "
            "FROM knowledge_bases k WHERE k.status=$1 "
            "AND (k.lease_expires_at IS NULL OR k.lease_expires_at<=clock_timestamp()) "
            "AND (NOT $2 OR k.cleanup_verified_at IS NULL OR "
            "k.cleanup_verified_at<=clock_timestamp()-make_interval(secs=>$3)) "
            "UNION ALL SELECT k.owner_id,k.kb_id,d.document_id,"
            "COALESCE(d.cleanup_verified_at,d.updated_at) FROM documents d "
            "JOIN knowledge_bases k USING(kb_id) WHERE d.status=$1 AND k.status<>'deleted' "
            "AND (d.lease_expires_at IS NULL OR d.lease_expires_at<=clock_timestamp()) "
            "AND (NOT $2 OR d.cleanup_verified_at IS NULL OR "
            "d.cleanup_verified_at<=clock_timestamp()-make_interval(secs=>$3)) "
            "ORDER BY scanned_at,kb_id,document_id LIMIT $4",
            status,
            tombstone,
            interval_s,
            limit,
        )
        return [
            CleanupCandidate(
                owner_id=row["owner_id"], kb_id=row["kb_id"], document_id=row["document_id"]
            )
            for row in rows
        ]

    async def check_deletion(self, owner, kb_id, token, revision, tx, *, document_id=None):
        _, _, _, _, resource, _ = await self._deletion_fence(
            owner, kb_id, token, revision, tx, document_id
        )
        return resource

    async def _deletion_fence(self, owner, kb_id, token, revision, tx, document_id):
        self._fence_parameters(token, revision)
        conn, table, identity, model, resource = await self._lifecycle_resource(
            owner, kb_id, document_id, tx
        )
        now = await conn.fetchval("SELECT clock_timestamp()")
        if (
            resource.status not in {"deleting", "deleted"}
            or resource.lease_token != token
            or resource.revision != revision
            or resource.lease_owner is None
            or resource.lease_expires_at <= now
        ):
            raise AppError("stale_resource", "Deletion lease or revision is not current")
        return conn, table, identity, model, resource, now

    async def deletion_context(self, owner, kb_id, token, revision, tx, *, document_id=None):
        from infrastructure.storage.research_postgres import decode

        conn, _, _, _, resource, _ = await self._deletion_fence(
            owner, kb_id, token, revision, tx, document_id
        )
        kb = resource if document_id is None else await self.get_kb(owner, kb_id, tx)
        rows = await conn.fetch(
            "SELECT * FROM document_versions WHERE kb_id=$1 "
            "AND ($2::uuid IS NULL OR document_id=$2) ORDER BY document_version_id",
            kb_id,
            document_id,
        )
        return DeletionContext(
            kb=kb,
            document=resource if document_id else None,
            versions=[decode(DocumentVersion, row, set()) for row in rows],
        )

    async def _quiet(self, conn, kb_id, document_id, resource, now):
        if resource.cleanup_not_before is not None and resource.cleanup_not_before > now:
            return False
        return not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM ingestion_jobs WHERE kb_id=$1 "
            "AND ($2::uuid IS NULL OR document_id=$2) AND lease_expires_at>clock_timestamp()) "
            "OR ($2::uuid IS NULL AND EXISTS(SELECT 1 FROM documents WHERE kb_id=$1 "
            "AND lease_expires_at>clock_timestamp()))",
            kb_id,
            document_id,
        )

    async def prepare_deletion(self, owner, kb_id, token, revision, tx, *, document_id=None):
        conn, _, _, _, resource, now = await self._deletion_fence(
            owner, kb_id, token, revision, tx, document_id
        )
        if resource.status != "deleting" or resource.cleanup_cursor != "wait_jobs":
            raise AppError("invalid_state", "Deletion is not waiting for old work")
        jobs = await conn.fetch(
            "SELECT job_id FROM ingestion_jobs WHERE kb_id=$1 "
            "AND ($2::uuid IS NULL OR document_id=$2) AND status IN ('accepted','processing','cancelling') "
            "ORDER BY document_id,job_id",
            kb_id,
            document_id,
        )
        for job in jobs:
            await self.request_job_cancel(owner, job["job_id"], tx)
        ready = await self._quiet(conn, kb_id, document_id, resource, now)
        # Fair scan order even when a live old worker prevents cleanup.
        table, identity = (
            ("knowledge_bases", "kb_id") if document_id is None else ("documents", "document_id")
        )
        await conn.execute(
            f"UPDATE {table} SET updated_at=clock_timestamp() WHERE {identity}=$1",
            document_id or kb_id,
        )
        return ready

    async def record_deletion_failure(
        self, owner, kb_id, token, revision, failure, tx, *, document_id=None
    ):
        failure = Failure.model_validate(failure)
        conn, table, identity, _, resource, _ = await self._deletion_fence(
            owner, kb_id, token, revision, tx, document_id
        )
        # Keep the lease until expiry: a cancelled native request may still be in flight.
        row = await conn.fetchrow(
            f"UPDATE {table} SET failure=$2::jsonb,revision=revision+1,updated_at=clock_timestamp() "
            f"WHERE {identity}=$1 AND lease_token=$3 AND revision=$4 "
            "AND lease_owner IS NOT NULL AND lease_expires_at>clock_timestamp() RETURNING *",
            getattr(resource, identity),
            failure.model_dump_json(),
            token,
            revision,
        )
        if row is None:
            raise AppError("stale_resource", "Deletion failure lease is not current")

    async def finish_deletion(
        self,
        owner,
        kb_id,
        token,
        revision,
        tx,
        *,
        document_id=None,
        index_verified=False,
        objects_verified=False,
    ):
        from infrastructure.storage.research_postgres import decode

        conn, table, identity, model, resource, now = await self._deletion_fence(
            owner, kb_id, token, revision, tx, document_id
        )
        if index_verified is not True or objects_verified is not True:
            raise AppError(
                "service_not_ready", "External deletion must be verified", retryable=True
            )
        if resource.status == "deleted":
            row = await conn.fetchrow(
                f"UPDATE {table} SET cleanup_verified_at=clock_timestamp(),"
                "revision=revision+CASE WHEN failure IS NULL THEN 0 ELSE 1 END,"
                "updated_at=CASE WHEN failure IS NULL THEN updated_at ELSE clock_timestamp() END,failure=NULL,"
                f"lease_owner=NULL,lease_expires_at=NULL WHERE {identity}=$1 AND lease_token=$2 "
                "AND revision=$3 AND lease_owner IS NOT NULL AND lease_expires_at>clock_timestamp() RETURNING *",
                getattr(resource, identity),
                token,
                revision,
            )
            if row is None:
                raise AppError("stale_resource", "Tombstone sweep lease is not current")
            return decode(model, row, {"failure"})
        if resource.cleanup_cursor != "metadata" or not await self._quiet(
            conn, kb_id, document_id, resource, now
        ):
            raise AppError("stale_resource", "Deletion is not at a quiet metadata boundary")
        await conn.execute(
            "DELETE FROM chunks WHERE kb_id=$1 AND ($2::uuid IS NULL OR document_id=$2)",
            kb_id,
            document_id,
        )
        await conn.execute(
            "UPDATE document_versions SET status=CASE WHEN status='active' THEN 'retired' ELSE 'failed' END,"
            "parsed_object_key=CASE WHEN status='active' THEN parsed_object_key ELSE NULL END,"
            "manifest_object_key=CASE WHEN status='active' THEN manifest_object_key ELSE NULL END,"
            "chunk_count=CASE WHEN status='active' THEN chunk_count ELSE 0 END "
            "WHERE kb_id=$1 AND ($2::uuid IS NULL OR document_id=$2) AND status<>'retired'",
            kb_id,
            document_id,
        )
        jobs = await conn.fetch(
            "SELECT * FROM ingestion_jobs WHERE kb_id=$1 AND ($2::uuid IS NULL OR document_id=$2) "
            "AND status NOT IN ('completed','cancelled') ORDER BY document_id,job_id FOR UPDATE",
            kb_id,
            document_id,
        )
        for row in jobs:
            job = decode(IngestionJob, row, {"failure", "progress", "attempt_history"})
            history = list(job.attempt_history)
            if history and history[-1].status == "processing":
                history[-1] = JobAttempt.model_validate(
                    history[-1].model_dump() | {"status": "cancelled", "finished_at": now}
                )
            cancelled = IngestionJob.model_validate(
                job.model_dump()
                | {
                    "status": "cancelled",
                    "cancel_requested_at": job.cancel_requested_at or now,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "lease_token": job.lease_token + 1,
                    "finished_at": now,
                    "attempt_history": history,
                    "progress": {
                        "step": "cleanup",
                        "completed_units": 0,
                        "total_units": None,
                        "completed_batches": [],
                    },
                }
            )
            await self._save_job(conn, cancelled)
        if document_id is None:
            await conn.execute(
                "UPDATE documents SET status='deleted',active_version_id=NULL,cleanup_cursor=NULL,"
                "cleanup_not_before=NULL,cleanup_verified_at=clock_timestamp(),failure=NULL,"
                "lease_owner=NULL,lease_expires_at=NULL,revision=revision+1,updated_at=clock_timestamp() "
                "WHERE kb_id=$1 AND status<>'deleted'",
                kb_id,
            )
        # Final SQL-clock CAS after all preceding metadata work. Failure rolls the
        # entire transaction back, including versions, Jobs and child Documents.
        active_pointer = "active_version_id=NULL," if document_id is not None else ""
        row = await conn.fetchrow(
            f"UPDATE {table} SET {active_pointer}status='deleted',cleanup_cursor=NULL,cleanup_not_before=NULL,"
            "cleanup_verified_at=clock_timestamp(),failure=NULL,lease_owner=NULL,lease_expires_at=NULL,"
            "revision=revision+1,updated_at=clock_timestamp() "
            f"WHERE {identity}=$1 AND lease_token=$2 AND revision=$3 AND lease_owner IS NOT NULL "
            "AND lease_expires_at>clock_timestamp() AND status='deleting' RETURNING *",
            getattr(resource, identity),
            token,
            revision,
        )
        if row is None:
            raise AppError("stale_resource", "Deletion final lease is not current")
        return decode(model, row, {"failure"})
