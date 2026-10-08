"""Short management transactions; deletion barriers are not physical cleanup."""

from application.errors import AppError
from application.knowledge_models import Document, KnowledgeBase, KnowledgeBasePatch


class KnowledgeManagement:
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
