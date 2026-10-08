"""Knowledge facts share the research store's transaction and ownership rules."""

from datetime import timedelta

from application.errors import AppError
from application.knowledge_models import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    IngestionProgress,
    JobAttempt,
    KnowledgeBase,
)
from infrastructure.storage.knowledge_cleanup import KnowledgeCleanup
from infrastructure.storage.knowledge_jobs import KnowledgeJobs
from infrastructure.storage.knowledge_management import KnowledgeManagement


class KnowledgeRepository(KnowledgeJobs, KnowledgeManagement, KnowledgeCleanup):
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _scoped_content(key, owner, kb_id, version_id, digest=None):
        from infrastructure.storage.content import content_key

        try:
            return content_key(key).startswith(
                f"knowledge-content/{owner}/{kb_id}/{version_id}/"
            ) and (digest is None or key.rsplit("/", 1)[-1] == digest)
        except (TypeError, ValueError):
            return False

    async def create_kb(self, kb: KnowledgeBase, tx):
        from infrastructure.storage.research_postgres import encode, insert

        kb = KnowledgeBase.model_validate(kb)
        await insert(self.store.connection(tx), "knowledge_bases", encode(kb, {"failure"}))

    async def get_kb(self, owner, kb_id, tx=None, *, for_update=False):
        from infrastructure.storage.research_postgres import decode

        if for_update and tx is None:
            raise ValueError("Locking requires a transaction")
        async with self.store.read(tx) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM knowledge_bases WHERE owner_id=$1 AND kb_id=$2"
                + (" FOR UPDATE" if for_update else ""),
                owner,
                kb_id,
            )
        return decode(KnowledgeBase, row, {"failure"})

    async def get_job(self, owner, job_id, tx=None):
        from infrastructure.storage.research_postgres import decode

        async with self.store.read(tx) as conn:
            row = await conn.fetchrow(
                "SELECT j.* FROM ingestion_jobs j JOIN knowledge_bases k USING(kb_id) "
                "WHERE k.owner_id=$1 AND j.job_id=$2",
                owner,
                job_id,
            )
        return decode(IngestionJob, row, {"failure", "progress", "attempt_history"})

    async def job_context(self, owner, job_id, tx):
        from application.knowledge_models import IngestionJobContext
        from infrastructure.storage.research_postgres import decode

        conn, kb, document, job, _ = await self._lock_job(owner, job_id, tx, require_active=False)
        version = decode(
            DocumentVersion,
            await conn.fetchrow(
                "SELECT * FROM document_versions WHERE document_version_id=$1",
                job.document_version_id,
            ),
            set(),
        )
        return IngestionJobContext(kb=kb, document=document, version=version, job=job)

    async def submit(self, owner, document, version, job, tx, *, create_document: bool):
        """Accept uploaded immutable content, deduplicate, or fail without partial facts."""
        from infrastructure.storage.research_postgres import decode, encode, insert

        document = Document.model_validate(document)
        version = DocumentVersion.model_validate(version)
        job = IngestionJob.model_validate(job)
        if (
            document.kb_id != version.kb_id
            or document.document_id != version.document_id
            or job.document_version_id != version.document_version_id
            or version.status != "staging"
            or job.status != "accepted"
            or job.attempt_count
            or job.lease_owner is not None
        ):
            raise AppError("invalid_state", "Invalid ingestion submission")
        kb = await self.get_kb(owner, version.kb_id, tx, for_update=True)
        if kb is None:
            raise AppError("knowledge_base_not_found", "Knowledge base not found")
        if kb.status != "active":
            raise AppError("resource_not_active", "Knowledge base is not active")
        if version.index_version != kb.index_version:
            raise AppError("invalid_state", "Index profile differs from knowledge base")
        if not self._scoped_content(
            version.source_object_key,
            owner,
            kb.kb_id,
            version.document_version_id,
            version.content_hash,
        ):
            raise AppError("invalid_state", "Source content has wrong scope or hash")
        conn = self.store.connection(tx)
        existing = decode(
            DocumentVersion,
            await conn.fetchrow(
                "SELECT * FROM document_versions WHERE kb_id=$1 AND content_hash=$2 AND ingestion_version=$3",
                version.kb_id,
                version.content_hash,
                version.ingestion_version,
            ),
            set(),
        )
        if existing is not None:
            if not create_document and existing.document_id != document.document_id:
                raise AppError("content_identity_conflict", "Content belongs to another document")
            if (
                await conn.fetchval(
                    "SELECT status FROM documents WHERE document_id=$1", existing.document_id
                )
                != "active"
            ):
                raise AppError("resource_not_active", "Document is not active")
            old_job = await conn.fetchrow(
                "SELECT * FROM ingestion_jobs WHERE document_version_id=$1",
                existing.document_version_id,
            )
            if old_job is None:
                raise AppError("content_unavailable", "Ingestion identity is incomplete")
            return (
                existing,
                decode(IngestionJob, old_job, {"failure", "progress", "attempt_history"}),
                True,
            )
        current = decode(
            Document,
            await conn.fetchrow(
                "SELECT * FROM documents WHERE document_id=$1 AND kb_id=$2 FOR UPDATE",
                document.document_id,
                document.kb_id,
            ),
            {"failure"},
        )
        if create_document:
            if (
                current is not None
                or document.status != "active"
                or document.active_version_id is not None
            ):
                raise AppError("invalid_state", "New document identity is invalid")
            await insert(conn, "documents", encode(document, {"failure"}))
        elif current is None:
            raise AppError("document_not_found", "Document not found")
        elif current.status != "active":
            raise AppError("resource_not_active", "Document is not active")
        await insert(conn, "document_versions", encode(version, set()))
        await insert(
            conn,
            "ingestion_jobs",
            encode(job, {"failure", "progress", "attempt_history"})
            | {
                "document_id": document.document_id,
                "kb_id": document.kb_id,
            },
        )
        return version, job, False

    async def _lock_job(self, owner, job_id, tx, *, require_active=True):
        from infrastructure.storage.research_postgres import decode

        conn = self.store.connection(tx)
        scope = await conn.fetchrow(
            "SELECT j.kb_id,j.document_id FROM ingestion_jobs j JOIN knowledge_bases k USING(kb_id) "
            "WHERE j.job_id=$1 AND k.owner_id=$2",
            job_id,
            owner,
        )
        if scope is None:
            raise AppError("job_not_found", "Ingestion job not found")
        kb = await self.get_kb(owner, scope["kb_id"], tx, for_update=True)
        document = decode(
            Document,
            await conn.fetchrow(
                "SELECT * FROM documents WHERE document_id=$1 FOR UPDATE",
                scope["document_id"],
            ),
            {"failure"},
        )
        row = await conn.fetchrow("SELECT * FROM ingestion_jobs WHERE job_id=$1 FOR UPDATE", job_id)
        job = decode(IngestionJob, row, {"failure", "progress", "attempt_history"})
        if require_active and (kb.status != "active" or document.status != "active"):
            raise AppError("resource_not_active", "Ingestion resource is not active")
        now = await conn.fetchval("SELECT clock_timestamp()")
        return conn, kb, document, job, now

    async def _save_job(self, conn, job, *, expected_token=None, expected_status="processing"):
        from infrastructure.storage.research_postgres import encode

        data = encode(IngestionJob.model_validate(job), {"failure", "progress", "attempt_history"})
        identity = data.pop("job_id")
        assignments = ",".join(f"{key}=${i}" for i, key in enumerate(data, 2))
        values = [identity, *data.values()]
        guard = ""
        if expected_token is not None:
            values.append(expected_token)
            token_parameter = len(values)
            values.append(expected_status)
            guard = (
                f" AND lease_token=${token_parameter} AND status=${len(values)}"
                " AND lease_expires_at>clock_timestamp()"
            )
            if expected_status == "processing":
                guard += " AND cancel_requested_at IS NULL"
        changed = await conn.fetchval(
            f"UPDATE ingestion_jobs SET {assignments} WHERE job_id=$1{guard} RETURNING job_id",
            *values,
        )
        if changed is None:
            raise AppError("stale_resource", "Ingestion lease is not current")

    async def claim_job(self, owner, job_id, worker, tx, *, lease_s=30):
        if (
            not isinstance(worker, str)
            or not worker.strip()
            or type(lease_s) is not int
            or not 1 <= lease_s <= 300
        ):
            raise ValueError("Invalid ingestion lease")
        conn, _, _, job, now = await self._lock_job(owner, job_id, tx)
        if (
            job.status != "accepted"
            or job.attempt_count >= 3
            or job.cancel_requested_at is not None
        ):
            raise AppError("invalid_session_state", "Job is not claimable")
        attempt = JobAttempt(
            attempt=job.attempt_count + 1,
            started_at=now,
            finished_at=None,
            status="processing",
            failure=None,
            progress=job.progress,
        )
        claimed = IngestionJob.model_validate(
            job.model_dump()
            | {
                "status": "processing",
                "attempt_count": attempt.attempt,
                "attempt_history": [*job.attempt_history, attempt],
                "lease_owner": worker,
                "lease_token": job.lease_token + 1,
                "lease_expires_at": now + timedelta(seconds=lease_s),
                "started_at": job.started_at or now,
                "finished_at": None,
                "failure": None,
            }
        )
        await self._save_job(conn, claimed)
        return claimed

    @staticmethod
    def _fence(job, token, now):
        if (
            type(token) is not int
            or job.status != "processing"
            or job.cancel_requested_at is not None
            or job.lease_token != token
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise AppError("stale_resource", "Ingestion lease is not current")

    async def activate(self, owner, job_id, token, chunks, parsed_key, manifest_key, tx):
        """Caller has verified objects/index; publish all PG facts in this transaction."""
        from infrastructure.storage.research_postgres import decode, encode, insert

        conn, kb, document, job, now = await self._lock_job(owner, job_id, tx)
        self._fence(job, token, now)
        version = decode(
            DocumentVersion,
            await conn.fetchrow(
                "SELECT * FROM document_versions WHERE document_version_id=$1 FOR UPDATE",
                job.document_version_id,
            ),
            set(),
        )
        if version.status != "staging" or version.index_version != kb.index_version:
            raise AppError("invalid_state", "Version is not publishable")
        chunks = [Chunk.model_validate(chunk) for chunk in chunks]
        if (
            not chunks
            or len(chunks) > 10000
            or [chunk.ordinal for chunk in chunks] != list(range(len(chunks)))
            or any(
                chunk.document_version_id != version.document_version_id
                or chunk.document_id != document.document_id
                or chunk.kb_id != kb.kb_id
                or not self._scoped_content(
                    chunk.content_object_key,
                    owner,
                    kb.kb_id,
                    version.document_version_id,
                    chunk.content_hash,
                )
                for chunk in chunks
            )
            or any(
                not self._scoped_content(key, owner, kb.kb_id, version.document_version_id)
                for key in (parsed_key, manifest_key)
            )
            or len({chunk.chunk_id for chunk in chunks}) != len(chunks)
        ):
            raise AppError("invalid_state", "Chunk manifest is incomplete or has wrong scope")
        for chunk in chunks:
            existing = decode(
                Chunk,
                await conn.fetchrow("SELECT * FROM chunks WHERE chunk_id=$1", chunk.chunk_id),
                {"location", "metadata"},
            )
            if existing is not None and existing != chunk:
                raise AppError("invalid_state", "Chunk identity differs from immutable content")
            if existing is None:
                await insert(conn, "chunks", encode(chunk, {"location", "metadata"}))
        if await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_version_id=$1", version.document_version_id
        ) != len(chunks):
            raise AppError("invalid_state", "Stored chunks differ from manifest")
        await conn.execute(
            "UPDATE document_versions SET status='retired' WHERE document_id=$1 AND status='active'",
            document.document_id,
        )
        await conn.execute(
            "UPDATE document_versions SET status='active',activated_at=$2,chunk_count=$3,parsed_object_key=$4,manifest_object_key=$5 WHERE document_version_id=$1",
            version.document_version_id,
            now,
            len(chunks),
            parsed_key,
            manifest_key,
        )
        await conn.execute(
            "UPDATE documents SET active_version_id=$2,revision=revision+1,updated_at=$3 WHERE document_id=$1",
            document.document_id,
            version.document_version_id,
            now,
        )
        progress = IngestionProgress(
            step="commit",
            completed_units=len(chunks),
            total_units=len(chunks),
            completed_batches=job.progress.completed_batches,
        )
        attempt = JobAttempt.model_validate(
            job.attempt_history[-1].model_dump()
            | {"status": "completed", "finished_at": now, "progress": progress}
        )
        done = IngestionJob.model_validate(
            job.model_dump()
            | {
                "status": "completed",
                "finished_at": now,
                "lease_owner": None,
                "lease_expires_at": None,
                "progress": progress,
                "attempt_history": [*job.attempt_history[:-1], attempt],
            }
        )
        await self._save_job(conn, done, expected_token=token)
        return done

    async def fail_job(self, owner, job_id, token, failure, tx):
        """Record a failed attempt without changing the old published version."""
        from domain.research.models import Failure

        failure = Failure.model_validate(failure)
        conn, _, _, job, now = await self._lock_job(owner, job_id, tx)
        self._fence(job, token, now)
        await conn.execute(
            "UPDATE document_versions SET status='failed' WHERE document_version_id=$1 AND status='staging'",
            job.document_version_id,
        )
        attempt = JobAttempt.model_validate(
            job.attempt_history[-1].model_dump()
            | {
                "status": "failed",
                "finished_at": now,
                "failure": failure,
                "progress": job.progress,
            }
        )
        failed = IngestionJob.model_validate(
            job.model_dump()
            | {
                "status": "failed",
                "finished_at": now,
                "lease_owner": None,
                "lease_expires_at": None,
                "failure": failure,
                "attempt_history": [*job.attempt_history[:-1], attempt],
            }
        )
        await self._save_job(conn, failed, expected_token=token)
        return failed

    async def visible_chunks(self, owner, kb_id, tx=None):
        from infrastructure.storage.research_postgres import decode

        async with self.store.read(tx) as conn:
            rows = await conn.fetch(
                "SELECT c.* FROM chunks c JOIN document_versions v USING(document_version_id,document_id,kb_id) "
                "JOIN documents d USING(document_id,kb_id) JOIN knowledge_bases k USING(kb_id) "
                "WHERE k.owner_id=$1 AND k.kb_id=$2 AND k.status='active' AND d.status='active' "
                "AND v.status='active' AND d.active_version_id=v.document_version_id ORDER BY c.document_id,c.ordinal",
                owner,
                kb_id,
            )
        return [decode(Chunk, row, {"location", "metadata"}) for row in rows]
