"""Durable, owner-scoped cleanup; PG cursors and leases own every external step."""

import asyncio
import logging
from uuid import uuid4

from application.errors import AppError
from application.ports import (
    ContentStorePort,
    KnowledgeCleanupRepositoryPort,
    UnitOfWorkPort,
    VectorIndexPort,
)
from domain.ports import AdapterError, ClockPort
from domain.research.models import Failure

logger = logging.getLogger(__name__)


class KnowledgeCleanupService:
    def __init__(
        self,
        uow: UnitOfWorkPort,
        knowledge: KnowledgeCleanupRepositoryPort,
        index: VectorIndexPort,
        content: ContentStorePort,
        clock: ClockPort,
        *,
        lease_s=90,
        heartbeat_s=20,
        sweep_s=60,
    ):
        if not 0 < heartbeat_s < lease_s <= 300:
            raise ValueError("Invalid cleanup lease")
        self.uow, self.knowledge, self.index, self.content, self.clock = (
            uow,
            knowledge,
            index,
            content,
            clock,
        )
        self.lease_s, self.heartbeat_s, self.sweep_s = lease_s, heartbeat_s, sweep_s
        self.available = False
        self.wake = lambda: None

    async def tick(self):
        # Both inventories are PG-derived. A stuck deletion cannot starve tombstone GC.
        for tombstone in (False, True):
            async with self.uow.transaction() as tx:
                rows = await self.knowledge.scan_deletion(
                    tx, tombstone=tombstone, interval_s=self.sweep_s
                )
            if rows:
                await self._run(rows[0], tombstone=tombstone)

    async def _run(self, row, *, tombstone):
        held = None
        scope = {"document_id": row.document_id}
        try:
            async with self.uow.transaction() as tx:
                held = await self.knowledge.claim_lifecycle(
                    row.owner_id,
                    row.kb_id,
                    str(uuid4()),
                    tx,
                    lease_s=self.lease_s,
                    tombstone=tombstone,
                    **scope,
                )

            async def heartbeat():
                while True:
                    await asyncio.sleep(self.heartbeat_s)
                    async with self.uow.transaction() as tx:
                        await self.knowledge.renew_lifecycle(
                            row.owner_id,
                            row.kb_id,
                            held.lease_owner,
                            held.lease_token,
                            tx,
                            lease_s=self.lease_s,
                            **scope,
                        )

            execution = asyncio.create_task(
                self._clean(row, held, tombstone), name="dr4a-kb-cleanup"
            )
            renewal = asyncio.create_task(heartbeat(), name="dr4a-kb-cleanup-heartbeat")
            try:
                done, _ = await asyncio.wait(
                    (execution, renewal), return_when=asyncio.FIRST_COMPLETED
                )
                if execution in done:
                    await execution
                else:
                    await renewal
                    raise AppError("stale_resource", "Cleanup lease was lost")
            finally:
                execution.cancel()
                renewal.cancel()
                await asyncio.gather(execution, renewal, return_exceptions=True)
        except AppError as exc:
            if exc.code not in {"stale_resource", "resource_not_active", "document_busy"}:
                raise
        except (AdapterError, TimeoutError) as exc:
            if held is not None:
                async with self.uow.transaction() as tx:
                    # Read the current fenced revision: earlier cursor commits can advance it.
                    resource = (
                        await self.knowledge.get_document(
                            row.owner_id, row.kb_id, row.document_id, tx
                        )
                        if row.document_id
                        else await self.knowledge.get_kb(row.owner_id, row.kb_id, tx)
                    )
                    failure = Failure(
                        code="index_not_ready"
                        if isinstance(exc, AdapterError) and exc.dependency == "vector"
                        else "content_unavailable",
                        dependency="vector"
                        if isinstance(exc, AdapterError) and exc.dependency == "vector"
                        else "minio",
                        operation="delete_knowledge_content",
                        phase=None,
                        message="Knowledge cleanup dependency is unavailable",
                        retryable=True,
                        resume_allowed=False,
                        attempt=held.lease_token,
                        occurred_at=self.clock.now_utc(),
                        details=None,
                    )
                    try:
                        await self.knowledge.record_deletion_failure(
                            row.owner_id,
                            row.kb_id,
                            held.lease_token,
                            resource.revision,
                            failure,
                            tx,
                            **scope,
                        )
                    except AppError as fenced:
                        if fenced.code != "stale_resource":
                            raise
            logger.warning("knowledge_cleanup_dependency_failed")

    async def _clean(self, row, held, tombstone):
        owner, kb_id = row.owner_id, row.kb_id
        scope = {"document_id": row.document_id}

        async def check():
            async with self.uow.transaction() as tx:
                await self.knowledge.check_deletion(
                    owner, kb_id, held.lease_token, held.revision, tx, **scope
                )

        async def context():
            async with self.uow.transaction() as tx:
                value = await self.knowledge.deletion_context(
                    owner, kb_id, held.lease_token, held.revision, tx, **scope
                )
            return value

        async def cursor(next_cursor):
            nonlocal held
            async with self.uow.transaction() as tx:
                held = await self.knowledge.commit_cleanup_cursor(
                    owner, kb_id, held.lease_token, held.revision, next_cursor, tx, **scope
                )

        if not tombstone and held.cleanup_cursor == "wait_jobs":
            async with self.uow.transaction() as tx:
                ready = await self.knowledge.prepare_deletion(
                    owner, kb_id, held.lease_token, held.revision, tx, **scope
                )
                if not ready:
                    await self.knowledge.release_lifecycle(
                        owner, kb_id, held.lease_owner, held.lease_token, tx, **scope
                    )
                    return
            await cursor("index")
        value = await context()
        # Reverify the index on recovery, even if the cursor already advanced.
        # Expired old writes cannot be fenced by PG alone; tombstone sweeps cover late arrivals.
        if row.document_id is None:
            await self.index.drop_partition(kb_id)
        else:
            for version in value.versions:
                await check()
                await self.index.delete_version(kb_id, version.document_version_id)
        if not tombstone and held.cleanup_cursor == "index":
            await cursor("objects")
        await check()
        prefixes = (
            [f"knowledge-content/{owner}/{kb_id}/"]
            if row.document_id is None
            else [
                f"knowledge-content/{owner}/{kb_id}/{v.document_version_id}/"
                for v in value.versions
            ]
        )
        for prefix in prefixes:
            await check()
            await self.content.delete_prefix(prefix)
        if not tombstone and held.cleanup_cursor == "objects":
            await cursor("metadata")
        async with self.uow.transaction() as tx:
            await self.knowledge.finish_deletion(
                owner,
                kb_id,
                held.lease_token,
                held.revision,
                tx,
                index_verified=True,
                objects_verified=True,
                **scope,
            )
