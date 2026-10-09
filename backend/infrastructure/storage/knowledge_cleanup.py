"""Fenced lifecycle leases; external cleanup remains the Service's responsibility."""

from application.errors import AppError
from application.knowledge_models import Document, KnowledgeBase

CURSORS = ("wait_jobs", "index", "objects", "metadata")


class KnowledgeCleanup:
    async def scan_creating(self, tx, *, limit=20):
        from infrastructure.storage.research_postgres import decode

        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid creation scan limit")
        rows = await self.store.connection(tx).fetch(
            "SELECT * FROM knowledge_bases WHERE status='creating' "
            "AND (lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp()) "
            "ORDER BY updated_at,kb_id LIMIT $1",
            limit,
        )
        return [decode(KnowledgeBase, row, {"failure"}) for row in rows]

    async def record_creation_failure(self, owner, kb_id, token, failure, tx):
        from domain.research.models import Failure

        if type(token) is not int or token < 1:
            raise ValueError("Invalid creation fence")
        failure = Failure.model_validate(failure)
        kb = await self.get_kb(owner, kb_id, tx, for_update=True)
        if kb is None:
            raise AppError("knowledge_base_not_found", "Knowledge base not found")
        row = await self.store.connection(tx).fetchrow(
            "UPDATE knowledge_bases SET failure=$4::jsonb,revision=revision+1,updated_at=clock_timestamp() "
            "WHERE owner_id=$1 AND kb_id=$2 AND lease_token=$3 AND status='creating' "
            "AND lease_owner IS NOT NULL AND lease_expires_at>clock_timestamp() RETURNING kb_id",
            owner,
            kb_id,
            token,
            failure.model_dump_json(),
        )
        if row is None:
            raise AppError("stale_resource", "Creation failure lease is not current")

    async def scan_lifecycle(self, tx, *, limit=100):
        """Trusted worker inventory; no ownership or lease is granted by scanning."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid lifecycle scan limit")
        return await self.store.connection(tx).fetch(
            "SELECT 'kb' AS kind,k.owner_id,k.kb_id,NULL::uuid AS document_id,"
            "k.status,k.revision FROM knowledge_bases k "
            "WHERE k.status IN ('creating','deleting') "
            "AND (k.lease_expires_at IS NULL OR k.lease_expires_at<=clock_timestamp()) "
            "UNION ALL SELECT 'document',k.owner_id,k.kb_id,d.document_id,d.status,d.revision "
            "FROM documents d JOIN knowledge_bases k USING(kb_id) "
            "WHERE d.status='deleting' AND k.status<>'deleted' "
            "AND (d.lease_expires_at IS NULL OR d.lease_expires_at<=clock_timestamp()) "
            "ORDER BY kind,kb_id,document_id LIMIT $1",
            limit,
        )

    async def _lifecycle_resource(self, owner, kb_id, document_id, tx):
        from infrastructure.storage.research_postgres import decode

        kb = await self.get_kb(owner, kb_id, tx, for_update=True)
        if kb is None:
            raise AppError("knowledge_base_not_found", "Knowledge base not found")
        conn = self.store.connection(tx)
        if document_id is None:
            return conn, "knowledge_bases", "kb_id", KnowledgeBase, kb
        document = decode(
            Document,
            await conn.fetchrow(
                "SELECT * FROM documents WHERE kb_id=$1 AND document_id=$2 FOR UPDATE",
                kb_id,
                document_id,
            ),
            {"failure"},
        )
        if document is None:
            raise AppError("document_not_found", "Document not found")
        if kb.status == "deleted" and document.status != "deleted":
            raise AppError("resource_not_active", "Knowledge base is deleted")
        return conn, "documents", "document_id", Document, document

    @staticmethod
    def _lease_parameters(worker, lease_s):
        if (
            not isinstance(worker, str)
            or not worker.strip()
            or worker != worker.strip()
            or type(lease_s) is not int
            or not 1 <= lease_s <= 300
        ):
            raise ValueError("Invalid lifecycle lease")

    @staticmethod
    def _fence_parameters(token, revision):
        if type(token) is not int or token < 1 or type(revision) is not int or revision < 1:
            raise ValueError("Invalid lifecycle fence")

    async def claim_lifecycle(
        self, owner, kb_id, worker, tx, *, document_id=None, lease_s=30, tombstone=False
    ):
        from infrastructure.storage.research_postgres import decode

        self._lease_parameters(worker, lease_s)
        conn, table, identity, model, resource = await self._lifecycle_resource(
            owner, kb_id, document_id, tx
        )
        if type(tombstone) is not bool:
            raise TypeError("Tombstone mode must be explicit")
        if resource.status not in ({"deleted"} if tombstone else {"creating", "deleting"}):
            raise AppError("resource_not_active", "Resource is not awaiting lifecycle work")
        now = await conn.fetchval("SELECT clock_timestamp()")
        if resource.lease_expires_at is not None and resource.lease_expires_at > now:
            if resource.lease_owner == worker:
                return resource
            raise AppError("document_busy", "Lifecycle worker still owns this resource")
        row = await conn.fetchrow(
            f"UPDATE {table} SET lease_owner=$2,lease_token=lease_token+1,"
            f"lease_expires_at=clock_timestamp()+make_interval(secs=>$3) WHERE {identity}=$1 "
            "AND (lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp()) RETURNING *",
            getattr(resource, identity),
            worker,
            lease_s,
        )
        if row is None:
            raise AppError("stale_resource", "Lifecycle lease changed")
        return decode(model, row, {"failure"})

    async def release_lifecycle(self, owner, kb_id, worker, token, tx, *, document_id=None):
        from infrastructure.storage.research_postgres import decode

        self._lease_parameters(worker, 30)
        if type(token) is not int or token < 1:
            raise ValueError("Invalid lifecycle token")
        conn, table, identity, model, resource = await self._lifecycle_resource(
            owner, kb_id, document_id, tx
        )
        row = await conn.fetchrow(
            f"UPDATE {table} SET lease_owner=NULL,lease_expires_at=NULL WHERE {identity}=$1 "
            "AND lease_owner=$2 AND lease_token=$3 AND lease_expires_at>clock_timestamp() "
            "AND status IN ('creating','deleting','deleted') RETURNING *",
            getattr(resource, identity),
            worker,
            token,
        )
        if row is None:
            raise AppError("stale_resource", "Lifecycle lease is not current")
        return decode(model, row, {"failure"})

    async def renew_lifecycle(
        self, owner, kb_id, worker, token, tx, *, document_id=None, lease_s=30
    ):
        from infrastructure.storage.research_postgres import decode

        self._lease_parameters(worker, lease_s)
        if type(token) is not int or token < 1:
            raise ValueError("Invalid lifecycle token")
        conn, table, identity, model, resource = await self._lifecycle_resource(
            owner, kb_id, document_id, tx
        )
        row = await conn.fetchrow(
            f"UPDATE {table} SET lease_expires_at=GREATEST(lease_expires_at,"
            f"clock_timestamp()+make_interval(secs=>$4)) WHERE {identity}=$1 "
            "AND lease_owner=$2 AND lease_token=$3 AND lease_expires_at>clock_timestamp() "
            "AND status IN ('creating','deleting','deleted') RETURNING *",
            getattr(resource, identity),
            worker,
            token,
            lease_s,
        )
        if row is None:
            raise AppError("stale_resource", "Lifecycle lease is not current")
        return decode(model, row, {"failure"})

    async def commit_cleanup_cursor(
        self, owner, kb_id, token, revision, cursor, tx, *, document_id=None
    ):
        from infrastructure.storage.research_postgres import decode

        self._fence_parameters(token, revision)
        if cursor not in CURSORS:
            raise ValueError("Invalid cleanup cursor")
        conn, table, identity, model, resource = await self._lifecycle_resource(
            owner, kb_id, document_id, tx
        )
        if resource.status != "deleting" or resource.cleanup_cursor not in CURSORS:
            raise AppError("resource_not_active", "Resource is not awaiting cleanup")
        # Only the next safe point can be committed. Repeated same point is a
        # read-only success, but still requires the live token and revision.
        previous = CURSORS.index(resource.cleanup_cursor)
        if CURSORS.index(cursor) not in {previous, previous + 1}:
            raise AppError("invalid_state", "Cleanup cursor cannot skip or regress")
        row = await conn.fetchrow(
            f"UPDATE {table} SET cleanup_cursor=$4,revision=revision+"
            "CASE WHEN cleanup_cursor=$4 THEN 0 ELSE 1 END,"
            "updated_at=CASE WHEN cleanup_cursor=$4 THEN updated_at ELSE clock_timestamp() END "
            f"WHERE {identity}=$1 AND lease_token=$2 AND revision=$3 "
            "AND lease_owner IS NOT NULL AND lease_expires_at>clock_timestamp() "
            "AND status='deleting' RETURNING *",
            getattr(resource, identity),
            token,
            revision,
            cursor,
        )
        if row is None:
            raise AppError("stale_resource", "Cleanup lease or revision is not current")
        return decode(model, row, {"failure"})

    async def finish_kb_creation(
        self, owner, kb_id, token, revision, tx, *, partition_verified=False
    ):
        from infrastructure.storage.research_postgres import decode

        self._fence_parameters(token, revision)
        conn, _, _, _, kb = await self._lifecycle_resource(owner, kb_id, None, tx)
        if kb.status != "creating":
            raise AppError("resource_not_active", "Knowledge base is not creating")
        if partition_verified is not True:
            raise AppError("service_not_ready", "Index partition must be verified", retryable=True)
        row = await conn.fetchrow(
            "UPDATE knowledge_bases SET status='active',revision=revision+1,"
            "cleanup_cursor=NULL,failure=NULL,lease_owner=NULL,lease_expires_at=NULL,"
            "updated_at=clock_timestamp() WHERE owner_id=$1 AND kb_id=$2 "
            "AND lease_token=$3 AND revision=$4 AND lease_owner IS NOT NULL "
            "AND lease_expires_at>clock_timestamp() AND status='creating' RETURNING *",
            owner,
            kb_id,
            token,
            revision,
        )
        if row is None:
            raise AppError("stale_resource", "Creation lease or revision is not current")
        return decode(KnowledgeBase, row, {"failure"})
