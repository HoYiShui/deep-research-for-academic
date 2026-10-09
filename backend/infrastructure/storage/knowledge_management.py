"""Short management transactions; deletion barriers are not physical cleanup."""

from application.errors import AppError
from application.knowledge_models import (
    Document,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeBasePatch,
)


class KnowledgeManagement:
    @staticmethod
    def _page(limit, after):
        # The extra row is used by the Service to determine next_cursor.
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Invalid page limit")
        return after if after is not None else (None, None)

    async def list_kbs(self, owner, tx, *, limit, after=None, status=None):
        from infrastructure.storage.research_postgres import decode

        if status not in {None, "creating", "active", "deleting", "deleted"}:
            raise ValueError("Invalid knowledge base status")
        date, identity = self._page(limit, after)
        rows = await self.store.connection(tx).fetch(
            "SELECT * FROM knowledge_bases WHERE owner_id=$1 "
            "AND (($2::text IS NULL AND status<>'deleted') OR status=$2) "
            "AND ($3::timestamptz IS NULL OR (created_at,kb_id)>($3,$4::uuid)) "
            "ORDER BY created_at,kb_id LIMIT $5",
            owner,
            status,
            date,
            identity,
            limit,
        )
        return [decode(KnowledgeBase, row, {"failure"}) for row in rows]

    async def get_document(self, owner, kb_id, document_id, tx):
        from infrastructure.storage.research_postgres import decode

        row = await self.store.connection(tx).fetchrow(
            "SELECT d.* FROM documents d JOIN knowledge_bases k USING(kb_id) "
            "WHERE k.owner_id=$1 AND d.kb_id=$2 AND d.document_id=$3",
            owner,
            kb_id,
            document_id,
        )
        return decode(Document, row, {"failure"})

    async def list_documents(self, owner, kb_id, tx, *, limit, after=None):
        from infrastructure.storage.research_postgres import decode

        date, identity = self._page(limit, after)
        rows = await self.store.connection(tx).fetch(
            "SELECT d.* FROM documents d JOIN knowledge_bases k USING(kb_id) "
            "WHERE k.owner_id=$1 AND d.kb_id=$2 "
            "AND ($3::timestamptz IS NULL OR (d.created_at,d.document_id)>($3,$4::uuid)) "
            "ORDER BY d.created_at,d.document_id LIMIT $5",
            owner,
            kb_id,
            date,
            identity,
            limit,
        )
        return [decode(Document, row, {"failure"}) for row in rows]

    async def list_versions(self, owner, kb_id, document_id, tx, *, limit, after=None):
        from infrastructure.storage.research_postgres import decode

        date, identity = self._page(limit, after)
        rows = await self.store.connection(tx).fetch(
            "SELECT v.* FROM document_versions v JOIN knowledge_bases k USING(kb_id) "
            "WHERE k.owner_id=$1 AND v.kb_id=$2 AND v.document_id=$3 "
            "AND ($4::timestamptz IS NULL OR (v.created_at,v.document_version_id)>($4,$5::uuid)) "
            "ORDER BY v.created_at,v.document_version_id LIMIT $6",
            owner,
            kb_id,
            document_id,
            date,
            identity,
            limit,
        )
        return [decode(DocumentVersion, row, set()) for row in rows]

    async def latest_job_id(self, owner, kb_id, document_id, tx):
        return await self.store.connection(tx).fetchval(
            "SELECT j.job_id FROM ingestion_jobs j JOIN document_versions v USING(document_version_id) "
            "JOIN knowledge_bases k ON k.kb_id=v.kb_id "
            "WHERE k.owner_id=$1 AND v.kb_id=$2 AND v.document_id=$3 "
            "ORDER BY j.created_at DESC,j.job_id DESC LIMIT 1",
            owner,
            kb_id,
            document_id,
        )

    async def update_kb(self, owner, kb_id, patch, tx):
        from infrastructure.storage.research_postgres import decode

        patch = KnowledgeBasePatch.model_validate(patch)
        kb = await self.get_kb(owner, kb_id, tx, for_update=True)
        if kb is None:
            raise AppError("knowledge_base_not_found", "Knowledge base not found")
        if kb.status != "active":
            raise AppError("resource_not_active", "Knowledge base is not active")
        if kb.revision != patch.revision:
            raise AppError("stale_resource", "Knowledge base revision has changed")
        # Omitted description preserves it; explicit null clears it.
        name = patch.name if "name" in patch.model_fields_set else kb.name
        description = (
            patch.description if "description" in patch.model_fields_set else kb.description
        )
        row = await self.store.connection(tx).fetchrow(
            "UPDATE knowledge_bases SET name=$3,description=$4,revision=revision+1,"
            "updated_at=clock_timestamp() WHERE owner_id=$1 AND kb_id=$2 "
            "AND revision=$5 AND status='active' RETURNING *",
            owner,
            kb_id,
            name,
            description,
            patch.revision,
        )
        if row is None:
            raise AppError("stale_resource", "Knowledge base revision has changed")
        return decode(KnowledgeBase, row, {"failure"})

    async def mark_kb_deleting(self, owner, kb_id, tx):
        from infrastructure.storage.research_postgres import decode

        kb = await self.get_kb(owner, kb_id, tx, for_update=True)
        if kb is None:
            raise AppError("knowledge_base_not_found", "Knowledge base not found")
        if kb.status in {"deleting", "deleted"}:
            return kb
        row = await self.store.connection(tx).fetchrow(
            "UPDATE knowledge_bases SET status='deleting',revision=revision+1,"
            "cleanup_cursor='wait_jobs',updated_at=clock_timestamp() "
            "WHERE owner_id=$1 AND kb_id=$2 AND revision=$3 RETURNING *",
            owner,
            kb_id,
            kb.revision,
        )
        if row is None:
            raise AppError("stale_resource", "Knowledge base revision has changed")
        return decode(KnowledgeBase, row, {"failure"})

    async def mark_document_deleting(self, owner, kb_id, document_id, tx):
        from infrastructure.storage.research_postgres import decode

        # Match ingestion lock order: parent KB before Document, never inverse.
        kb = await self.get_kb(owner, kb_id, tx, for_update=True)
        if kb is None:
            raise AppError("knowledge_base_not_found", "Knowledge base not found")
        conn = self.store.connection(tx)
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
        if document.status in {"deleting", "deleted"}:
            return document
        if kb.status != "active":
            raise AppError("resource_not_active", "Knowledge base is not active")
        row = await conn.fetchrow(
            "UPDATE documents SET status='deleting',revision=revision+1,"
            "cleanup_cursor='wait_jobs',updated_at=clock_timestamp() "
            "WHERE kb_id=$1 AND document_id=$2 AND revision=$3 RETURNING *",
            kb_id,
            document_id,
            document.revision,
        )
        if row is None:
            raise AppError("stale_resource", "Document revision has changed")
        return decode(Document, row, {"failure"})
