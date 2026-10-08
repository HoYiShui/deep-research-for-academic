"""Explicit knowledge metadata fixtures, not parsed/indexed content acceptance."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from application.knowledge_models import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
)


def knowledge_base(owner, *, status="active", name="Fixture KB"):
    now = datetime.now(UTC)
    return KnowledgeBase(
        kb_id=uuid4(),
        owner_id=owner,
        name=name,
        description=None,
        data_classification="private",
        status=status,
        revision=1,
        index_version="fixture-index-v1",
        cleanup_cursor=None,
        failure=None,
        lease_owner=None,
        lease_token=0,
        lease_expires_at=None,
        created_at=now,
        updated_at=now,
    )


def content_key(version, content):
    return version.source_object_key.rsplit("/", 1)[0] + "/" + sha256(content).hexdigest()


def submission(kb, document=None, *, body=b"Fixture source, not a real PDF"):
    now = datetime.now(UTC)
    document = document or Document(
        document_id=uuid4(),
        kb_id=kb.kb_id,
        filename="fixture.pdf",
        media_type="application/pdf",
        status="active",
        active_version_id=None,
        revision=1,
        cleanup_cursor=None,
        failure=None,
        lease_owner=None,
        lease_token=0,
        lease_expires_at=None,
        created_at=now,
        updated_at=now,
    )
    version_id = uuid4()
    digest = sha256(body).hexdigest()
    version = DocumentVersion(
        document_version_id=version_id,
        document_id=document.document_id,
        kb_id=kb.kb_id,
        content_hash=digest,
        ingestion_version="fixture-profile-v1",
        index_version=kb.index_version,
        source_object_key=f"knowledge-content/{kb.owner_id}/{kb.kb_id}/{version_id}/{digest}",
        parsed_object_key=None,
        manifest_object_key=None,
        chunk_count=0,
        status="staging",
        created_at=now,
        activated_at=None,
    )
    job = IngestionJob(
        job_id=uuid4(),
        document_version_id=version_id,
        idempotency_key=uuid4().hex,
        status="accepted",
        attempt_count=0,
        attempt_history=[],
        progress={
            "step": "upload",
            "completed_units": 0,
            "total_units": None,
            "completed_batches": [],
        },
        cancel_requested_at=None,
        lease_owner=None,
        lease_token=0,
        lease_expires_at=None,
        failure=None,
        created_at=now,
        started_at=None,
        finished_at=None,
    )
    return document, version, job


def chunk(version, *, ordinal=0):
    body = f"Fixture chunk {ordinal}".encode()
    return Chunk(
        chunk_id=f"chunk-{version.document_version_id}-{ordinal}",
        kb_id=version.kb_id,
        document_id=version.document_id,
        document_version_id=version.document_version_id,
        ordinal=ordinal,
        chunk_type="text",
        content_object_key=content_key(version, body),
        content_hash=sha256(body).hexdigest(),
        embedding_anchor="Fixture text, no real embedding",
        location={"page_start": 1},
        metadata={},
    )
