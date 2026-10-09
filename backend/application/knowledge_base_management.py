"""Owner-scoped PG management; index I/O never runs inside a database transaction.

Upload/retrieval stay explicitly unavailable until their workers are
composed. A successful creation proves the Standalone partition exists, not that
Parser/BGE/ingestion are ready.
"""

import asyncio
import base64
import binascii
import logging
from uuid import UUID, uuid4

from pydantic import ValidationError

from application.errors import AppError
from application.knowledge_models import (
    DocumentView,
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBaseView,
)
from application.ports import (
    KnowledgeManagementRepositoryPort,
    RequestStorePort,
    UnitOfWorkPort,
    VectorIndexPort,
)
from domain.ports import AdapterError, ClockPort
from domain.research.ids import canonical_hash
from domain.research.models import UTC, Failure, Hash, Record

logger = logging.getLogger(__name__)


class _Cursor(Record):
    scope: Hash
    created_at: UTC
    identity: UUID


def public_entity(value, *, identity):
    fields = {
        "kb": set(KnowledgeBaseView.model_fields),
        "document": {
            "document_id",
            "kb_id",
            "filename",
            "media_type",
            "status",
            "active_version_id",
            "revision",
            "failure",
            "created_at",
            "updated_at",
        },
        "version": {
            "document_version_id",
            "document_id",
            "kb_id",
            "content_hash",
            "ingestion_version",
            "index_version",
            "chunk_count",
            "status",
            "created_at",
            "activated_at",
        },
    }[identity]
    result = value.model_dump(mode="json", include=fields)
    if "failure" in fields:
        result["failure"] = (
            value.failure.model_dump(mode="json", exclude={"details"}) if value.failure else None
        )
    return result


class KnowledgeBaseManagementService:
    def __init__(
        self,
        uow: UnitOfWorkPort,
        knowledge: KnowledgeManagementRepositoryPort,
        requests: RequestStorePort,
        index: VectorIndexPort,
        clock: ClockPort,
        *,
        index_version: str,
        ingestion=None,
        cleanup=None,
        lease_s=90,
        heartbeat_s=20,
    ):
        if not 0 < heartbeat_s < lease_s <= 300:
            raise ValueError("Invalid lifecycle heartbeat/lease")
        self.uow, self.knowledge, self.requests = uow, knowledge, requests
        self.index, self.clock, self.index_version = index, clock, index_version
        self.ingestion, self.lease_s, self.heartbeat_s = ingestion, lease_s, heartbeat_s
        self.cleanup = cleanup

    async def _kb(self, owner, kb_id, tx):
        value = await self.knowledge.get_kb(owner, kb_id, tx)
        if value is None:
            raise AppError("knowledge_base_not_found", "Knowledge base not found")
        return value

    async def _document(self, owner, kb_id, document_id, tx):
        await self._kb(owner, kb_id, tx)
        value = await self.knowledge.get_document(owner, kb_id, document_id, tx)
        if value is None:
            raise AppError("document_not_found", "Document not found")
        return value

    async def get(self, owner, kb_id):
        async with self.uow.transaction() as tx:
            return public_entity(await self._kb(owner, kb_id, tx), identity="kb")

    @staticmethod
    def _after(cursor, scope):
        if cursor is None:
            return None
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
                raise ValueError("Invalid cursor length")
            raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
            value = _Cursor.model_validate_json(raw)
            if value.scope != scope:
                raise ValueError("Cursor scope differs")
            return value.created_at, value.identity
        except (ValueError, binascii.Error, ValidationError):
            raise AppError("validation_error", "Invalid pagination cursor") from None

    @staticmethod
    def _page(values, limit, scope, kind, identity):
        if len(values) > limit:
            last = values[limit - 1]
            cursor = _Cursor(
                scope=scope, created_at=last.created_at, identity=getattr(last, identity)
            )
            next_cursor = base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode()
        else:
            next_cursor = None
        return {
            "items": [public_entity(item, identity=kind) for item in values[:limit]],
            "next_cursor": next_cursor,
        }

    async def list(self, owner, *, limit=20, cursor=None, status=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AppError("validation_error", "Invalid page limit")
        scope = canonical_hash({"owner": str(owner), "kind": "kb", "status": status})
        after = self._after(cursor, scope)
        async with self.uow.transaction() as tx:
            values = await self.knowledge.list_kbs(
                owner, tx, limit=limit + 1, after=after, status=status
            )
        return self._page(values, limit, scope, "kb", "kb_id")

    async def documents(self, owner, kb_id, *, limit=20, cursor=None, document_id=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AppError("validation_error", "Invalid page limit")
        scope = canonical_hash(
            {
                "owner": str(owner),
                "kind": "document" if document_id is None else "version",
                "kb_id": str(kb_id),
                "document_id": str(document_id) if document_id else None,
            }
        )
        after = self._after(cursor, scope)
        async with self.uow.transaction() as tx:
            if document_id is None:
                await self._kb(owner, kb_id, tx)
                values = await self.knowledge.list_documents(
                    owner, kb_id, tx, limit=limit + 1, after=after
                )
            else:
                await self._document(owner, kb_id, document_id, tx)
                values = await self.knowledge.list_versions(
                    owner, kb_id, document_id, tx, limit=limit + 1, after=after
                )
        return self._page(
            values,
            limit,
            scope,
            "document" if document_id is None else "version",
            "document_id" if document_id is None else "document_version_id",
        )

    async def document(self, owner, kb_id, document_id):
        async with self.uow.transaction() as tx:
            document = await self._document(owner, kb_id, document_id, tx)
            versions = await self.knowledge.list_versions(owner, kb_id, document_id, tx, limit=100)
            job_id = await self.knowledge.latest_job_id(owner, kb_id, document_id, tx)
        if job_id is not None and self.ingestion is None:
            raise AppError("service_not_ready", "Job views are not configured", retryable=True)
        return {
            "document": public_entity(document, identity="document"),
            "versions": [public_entity(item, identity="version") for item in versions],
            "latest_job": await self.ingestion.get(owner, job_id) if job_id else None,
        }

    async def _release(self, reservation, held=None):
        try:
            async with self.uow.transaction() as tx:
                await self.requests.release(
                    reservation, tx, preserve_resource=reservation.resource_id is not None
                )
        except (AppError, AdapterError):
            logger.warning("knowledge_request_release_failed")
        if held is not None:
            try:
                async with self.uow.transaction() as tx:
                    await self.knowledge.release_lifecycle(
                        held.owner_id, held.kb_id, held.lease_owner, held.lease_token, tx
                    )
            except (AppError, AdapterError):
                logger.warning("knowledge_creation_lease_release_failed")

    async def _ensure(self, held):
        async def work():
            await self.index.ensure_schema(held.index_version)
            await self.index.ensure_partition(held.kb_id)

        async def heartbeat():
            while True:
                await asyncio.sleep(self.heartbeat_s)
                async with self.uow.transaction() as tx:
                    await self.knowledge.renew_lifecycle(
                        held.owner_id,
                        held.kb_id,
                        held.lease_owner,
                        held.lease_token,
                        tx,
                        lease_s=self.lease_s,
                    )

        execution = asyncio.create_task(work(), name="dr4a-create-index")
        renewal = asyncio.create_task(heartbeat(), name="dr4a-create-heartbeat")
        try:
            # SDK operations are bounded too. Stop rather than retain an HTTP request indefinitely.
            async with asyncio.timeout(min(60, self.lease_s)):
                done, _ = await asyncio.wait(
                    (execution, renewal), return_when=asyncio.FIRST_COMPLETED
                )
                if renewal in done:
                    await renewal
                    raise AppError("stale_resource", "Creation lease was lost")
                await execution
        finally:
            execution.cancel()
            renewal.cancel()
            await asyncio.gather(execution, renewal, return_exceptions=True)

    async def _creation_failed(self, held):
        if held is None:
            return
        failure = Failure(
            code="index_not_ready",
            dependency="vector",
            operation="create_knowledge_base",
            phase=None,
            message="Knowledge base index is unavailable",
            retryable=True,
            resume_allowed=False,
            attempt=held.lease_token,
            occurred_at=self.clock.now_utc(),
            details=None,
        )
        try:
            async with self.uow.transaction() as tx:
                await self.knowledge.record_creation_failure(
                    held.owner_id, held.kb_id, held.lease_token, failure, tx
                )
        except (AppError, AdapterError):
            logger.warning("knowledge_creation_failure_not_committed")

    async def recover_creating(self):
        """Scanner-derived inventory, no second queue and no paid model/tool work.

        Recover one resource per tick. Failed resources move to the back through
        updated_at; a permanently unavailable partition cannot starve all others.
        The request's durable binding is untouched; its next retry caches 201.
        """
        async with self.uow.transaction() as tx:
            candidates = await self.knowledge.scan_creating(tx, limit=1)
        if not candidates:
            return
        value, held, succeeded = candidates[0], None, False
        try:
            async with self.uow.transaction() as tx:
                held = await self.knowledge.claim_lifecycle(
                    value.owner_id, value.kb_id, str(uuid4()), tx, lease_s=self.lease_s
                )
            await self._ensure(held)
            async with self.uow.transaction() as tx:
                await self.knowledge.finish_kb_creation(
                    held.owner_id,
                    held.kb_id,
                    held.lease_token,
                    held.revision,
                    tx,
                    partition_verified=True,
                )
            succeeded = True
        except (AdapterError, TimeoutError):
            await self._creation_failed(held)
            logger.warning("knowledge_creation_recovery_failed")
        except AppError as exc:
            if exc.code not in {"document_busy", "resource_not_active", "stale_resource"}:
                raise
        finally:
            if held is not None and not succeeded:
                try:
                    async with self.uow.transaction() as tx:
                        await self.knowledge.release_lifecycle(
                            held.owner_id, held.kb_id, held.lease_owner, held.lease_token, tx
                        )
                except (AppError, AdapterError):
                    logger.warning("knowledge_creation_recovery_release_failed")

    async def create(self, owner, value: KnowledgeBaseCreate, key):
        value = KnowledgeBaseCreate.model_validate(value)
        now = self.clock.now_utc()
        async with self.uow.transaction() as tx:
            reservation = await self.requests.reserve(
                owner, "knowledge:create", key, canonical_hash(value.model_dump(mode="json")), tx
            )
            if reservation.resource_id is None:
                kb = KnowledgeBase(
                    kb_id=uuid4(),
                    owner_id=owner,
                    **value.model_dump(),
                    status="creating",
                    revision=1,
                    index_version=self.index_version,
                    cleanup_cursor=None,
                    failure=None,
                    lease_owner=None,
                    lease_token=0,
                    lease_expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
                await self.knowledge.create_kb(kb, tx)
                reservation = await self.requests.bind_resource(reservation, kb.kb_id, tx)
            else:
                kb = await self._kb(owner, reservation.resource_id, tx)
            if reservation.state == "completed":
                try:
                    cached = KnowledgeBaseView.model_validate(reservation.response_body)
                    if reservation.response_status != 201 or cached.kb_id != kb.kb_id:
                        raise ValueError("Cached creation identity differs")
                except (ValueError, ValidationError):
                    raise AppError(
                        "content_unavailable",
                        "Cached knowledge base is unavailable",
                        retryable=True,
                    ) from None
                return 201, cached.model_dump(mode="json")
        held = None
        succeeded = False
        try:
            if kb.status == "creating":
                async with self.uow.transaction() as tx:
                    held = await self.knowledge.claim_lifecycle(
                        owner, kb.kb_id, str(uuid4()), tx, lease_s=self.lease_s
                    )
                await self._ensure(held)
            elif kb.status != "active":
                raise AppError("resource_not_active", "Knowledge base is not active")
            async with self.uow.transaction() as tx:
                current = (
                    await self.knowledge.finish_kb_creation(
                        owner,
                        kb.kb_id,
                        held.lease_token,
                        held.revision,
                        tx,
                        partition_verified=True,
                    )
                    if held is not None
                    else await self._kb(owner, kb.kb_id, tx)
                )
                if current.status != "active":
                    raise AppError("resource_not_active", "Knowledge base is not active")
                body = public_entity(current, identity="kb")
                await self.requests.complete(reservation, 201, body, tx, resource_id=kb.kb_id)
            succeeded = True
            return 201, body
        except (AdapterError, TimeoutError):
            await self._creation_failed(held)
            raise AppError(
                "index_not_ready",
                "Knowledge base index is unavailable",
                retryable=True,
                details={"kb_id": str(kb.kb_id)},
            ) from None
        finally:
            if not succeeded:
                await self._release(reservation, held)

    async def update(self, owner, kb_id, patch, key):
        # Authorization before reading cached private responses.
        async with self.uow.transaction() as tx:
            await self._kb(owner, kb_id, tx)
            reservation = await self.requests.reserve(
                owner,
                f"knowledge:{kb_id}:update",
                key,
                canonical_hash(patch.model_dump(mode="json", exclude_unset=True)),
                tx,
            )
            if reservation.state == "completed":
                cached = KnowledgeBaseView.model_validate(reservation.response_body)
                if (
                    reservation.response_status != 200
                    or cached.kb_id != kb_id
                    or reservation.resource_id != kb_id
                ):
                    raise AppError(
                        "content_unavailable",
                        "Cached knowledge base is unavailable",
                        retryable=True,
                    )
                return 200, cached.model_dump(mode="json")
            # Single transaction: failed CAS rolls back both mutation and reservation.
            kb = await self.knowledge.update_kb(owner, kb_id, patch, tx)
            body = public_entity(kb, identity="kb")
            await self.requests.complete(reservation, 200, body, tx, resource_id=kb_id)
            return 200, body

    async def delete(self, owner, kb_id, key, *, document_id=None):
        async with self.uow.transaction() as tx:
            if document_id is None:
                await self._kb(owner, kb_id, tx)
            else:
                await self._document(owner, kb_id, document_id, tx)
            if self.cleanup is None or not self.cleanup.available:
                raise AppError(
                    "service_not_ready",
                    "Knowledge cleanup worker is not configured",
                    retryable=True,
                )
            identity = document_id or kb_id
            reservation = await self.requests.reserve(
                owner,
                f"knowledge:{kb_id}:delete:{document_id or 'kb'}",
                key,
                canonical_hash({}),
                tx,
            )
            if reservation.state == "completed":
                try:
                    cached = (DocumentView if document_id else KnowledgeBaseView).model_validate(
                        reservation.response_body
                    )
                    if (
                        reservation.response_status != (200 if cached.status == "deleted" else 202)
                        or cached.status not in {"deleting", "deleted"}
                        or reservation.resource_id != identity
                        or cached.kb_id != kb_id
                        or (document_id is not None and cached.document_id != document_id)
                    ):
                        raise ValueError("Cached deletion identity or status differs")
                    if cached.failure is not None:
                        if "details" in cached.failure:
                            raise ValueError("Private failure details are not public")
                        Failure.model_validate(cached.failure | {"details": None})
                except (ValueError, ValidationError):
                    raise AppError(
                        "content_unavailable", "Cached deletion is unavailable", retryable=True
                    ) from None
                return reservation.response_status, cached.model_dump(mode="json")
            value = (
                await self.knowledge.mark_document_deleting(owner, kb_id, document_id, tx)
                if document_id
                else await self.knowledge.mark_kb_deleting(owner, kb_id, tx)
            )
            status = 200 if value.status == "deleted" else 202
            body = public_entity(value, identity="document" if document_id else "kb")
            await self.requests.complete(reservation, status, body, tx, resource_id=identity)
        self.cleanup.wake()
        return status, body
