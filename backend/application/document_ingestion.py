"""Persistent job query/retry entry; upload/worker/cleanup composition is pending."""

import asyncio
import hashlib
import logging

from pydantic import ValidationError

from application.errors import AppError
from application.knowledge_models import JobAccepted
from application.ports import (
    ContentStorePort,
    IngestionJobRepositoryPort,
    RequestStorePort,
    UnitOfWorkPort,
)
from domain.ports import AdapterError
from domain.research.ids import canonical_hash

MAX_SOURCE_BYTES = 50 * 1024 * 1024


class DocumentIngestionService:
    def __init__(
        self,
        uow: UnitOfWorkPort,
        knowledge: IngestionJobRepositoryPort,
        requests: RequestStorePort,
        content: ContentStorePort,
    ):
        self.uow, self.knowledge, self.requests, self.content = uow, knowledge, requests, content

    async def _context(self, owner, job_id):
        async with self.uow.transaction() as tx:
            return await self.knowledge.job_context(owner, job_id, tx)

    @staticmethod
    def _retry_candidate(context):
        job = context.job
        return (
            context.kb.status == "active"
            and context.document.status == "active"
            and context.version.status == "failed"
            and job.attempt_count < 3
            and (
                job.status == "cancelled"
                or (
                    job.status == "failed"
                    and job.failure is not None
                    and job.failure.retryable
                    and job.failure.resume_allowed
                )
            )
        )

    async def _source(self, context, *, verify_body):
        version = context.version
        key = f"knowledge-content/{context.kb.owner_id}/{context.kb.kb_id}/{version.document_version_id}/{version.content_hash}"
        if version.source_object_key != key:
            raise AppError("content_unavailable", "Source content is unavailable", retryable=True)
        try:
            async with asyncio.timeout(60):
                reference = await self.content.head(key)
                if (
                    reference.sha256 != version.content_hash
                    or not 0 < reference.size <= MAX_SOURCE_BYTES
                ):
                    raise AppError(
                        "content_unavailable", "Source content is unavailable", retryable=True
                    )
                if verify_body:
                    digest, size = hashlib.sha256(), 0
                    async for chunk in await self.content.get(key):
                        size += len(chunk)
                        if size > MAX_SOURCE_BYTES:
                            raise AppError(
                                "content_unavailable",
                                "Source content is unavailable",
                                retryable=True,
                            )
                        digest.update(chunk)
                    if size != reference.size or digest.hexdigest() != version.content_hash:
                        raise AppError(
                            "content_unavailable", "Source content is unavailable", retryable=True
                        )
        except AdapterError as exc:
            if exc.code == "content_missing":
                return False
            raise AppError(
                "content_unavailable", "Source content is unavailable", retryable=True
            ) from None
        except TimeoutError:
            raise AppError(
                "content_unavailable", "Source verification timed out", retryable=True
            ) from None
        return True

    async def get(self, owner, job_id):
        context = await self._context(owner, job_id)
        job = context.job
        allowed = self._retry_candidate(context) and await self._source(context, verify_body=False)
        public = job.model_dump(
            mode="json",
            include={
                "job_id",
                "document_version_id",
                "status",
                "attempt_count",
                "progress",
                "cancel_requested_at",
                "created_at",
                "started_at",
                "finished_at",
            },
        )
        public.update(
            kb_id=str(context.kb.kb_id),
            document_id=str(context.document.document_id),
            retry_allowed=allowed,
            failure=job.failure.model_dump(mode="json", exclude={"details"})
            if job.failure
            else None,
        )
        return public

    async def retry(self, owner, job_id, key):
        context = await self._context(owner, job_id)  # authorize before cache or storage
        async with self.uow.transaction() as tx:
            reservation = await self.requests.reserve(
                owner, f"ingestion:{job_id}:retry", key, canonical_hash({}), tx
            )
        if reservation.state == "completed":
            try:
                accepted = JobAccepted.model_validate(reservation.response_body)
                if (
                    reservation.response_status != 202
                    or accepted.job_id != job_id
                    or reservation.resource_id != job_id
                ):
                    raise ValueError("Cached identity differs from the authorized request")
            except (ValidationError, ValueError):
                raise AppError(
                    "content_unavailable", "Cached job response is unavailable", retryable=True
                ) from None
            return 202, accepted.model_dump(mode="json")
        succeeded = False
        try:
            if not self._retry_candidate(context):
                if context.kb.status != "active" or context.document.status != "active":
                    raise AppError("resource_not_active", "Ingestion resource is not active")
                raise AppError("resume_not_allowed", "Job cannot be retried")
            verified = await self._source(context, verify_body=True)
            async with self.uow.transaction() as tx:
                job = await self.knowledge.retry_job(owner, job_id, tx, source_verified=verified)
                body = {"job_id": str(job.job_id), "status": "accepted"}
                await self.requests.complete(reservation, 202, body, tx, resource_id=job.job_id)
            succeeded = True
            return 202, body
        finally:
            if not succeeded:
                try:
                    async with self.uow.transaction() as tx:
                        await self.requests.release(reservation, tx)
                except (AdapterError, AppError):
                    logging.getLogger(__name__).warning("ingestion_request_release_failed")

    async def cancel(self, owner, job_id, key):
        context = await self._context(owner, job_id)
        if context.job.status == "completed":
            raise AppError("invalid_session_state", "Completed ingestion cannot be cancelled")
        if context.job.status == "cancelled":
            return 200, {"job_id": str(job_id), "status": "cancelled"}
        # No pretend successful cleanup or accepted work without a composed cleaner.
        raise AppError(
            "service_not_ready", "Ingestion cleanup worker is not configured", retryable=True
        )
