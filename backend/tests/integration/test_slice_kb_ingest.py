"""Integration tests for the KB-ingest slice (T040).

Verifies the parse -> chunk -> embed -> store pipeline, failure persistence,
stale-document recovery, and the /knowledge-base/documents list endpoint.
"""

from unittest.mock import patch

import pytest

from application.knowledge_base_service import KnowledgeBaseService, _chunk
from domain.ports import Embedding
from infrastructure.storage.memory import InMemoryDocumentStore


class _FakeParser:
    async def parse(self, path: str) -> dict:
        return {"text": "para one\n\npara two", "tables": [], "formulas": []}


class _FailingParser:
    async def parse(self, path: str) -> dict:
        raise RuntimeError("corrupt pdf")


class _FakeEmbedding:
    async def embed(self, text: str) -> Embedding:
        return Embedding(dense=[0.1], sparse={})


class _RecordingVector:
    def __init__(self) -> None:
        self.inserted: list[tuple] = []

    async def insert(self, kb_id: str, chunk_id: str, embedding: Embedding, metadata: dict) -> None:
        self.inserted.append((kb_id, chunk_id, metadata))


def test_chunk_merges_short_paragraphs() -> None:
    assert _chunk("p1\np2\np3", size=1000) == ["p1\np2\np3"]


def test_chunk_splits_when_size_exceeded() -> None:
    assert _chunk("hello world\nsecond para", size=10) == ["hello world", "second para"]


@pytest.mark.asyncio
async def test_ingest_parses_chunks_embeds_stores() -> None:
    vector = _RecordingVector()
    svc = KnowledgeBaseService(_FakeParser(), _FakeEmbedding(), vector, InMemoryDocumentStore())
    result = await svc.ingest("d1", "/tmp/x.pdf")
    assert result["status"] == "done"
    assert result["chunks"] == 1
    assert len(vector.inserted) == 1
    assert vector.inserted[0][0] == "default"  # kb_id
    assert vector.inserted[0][1] == "d1-0"  # chunk_id
    assert (await svc.get_document("d1"))["progress"] == 1.0


@pytest.mark.asyncio
async def test_ingest_failure_persists_failed_status() -> None:
    svc = KnowledgeBaseService(_FailingParser(), _FakeEmbedding(), _RecordingVector(),
                               InMemoryDocumentStore())
    result = await svc.ingest("d2", "/tmp/bad.pdf")
    assert result["status"] == "failed"
    assert (await svc.get_document("d2"))["status"] == "failed"


@pytest.mark.asyncio
async def test_recover_stale_marks_processing_as_failed() -> None:
    docs = InMemoryDocumentStore()
    await docs.save("d3", {"status": "processing", "progress": 0.5})
    await docs.save("d4", {"status": "done", "progress": 1.0})
    svc = KnowledgeBaseService(_FakeParser(), _FakeEmbedding(), _RecordingVector(), docs)
    await svc.recover_stale()
    assert (await docs.load("d3"))["status"] == "failed"
    assert (await docs.load("d4"))["status"] == "done"


def test_list_documents_endpoint() -> None:
    from fastapi.testclient import TestClient

    from interface.main import app

    class _KB:
        async def list_documents(self) -> list[dict]:
            return [{"document_id": "d1", "status": "done"}]

    class _Container:
        knowledge_base = _KB()

    with patch("interface.router.knowledge_base.get_container", return_value=_Container()):
        client = TestClient(app)
        resp = client.get("/knowledge-base/documents")

    assert resp.status_code == 200
    assert resp.json()["documents"][0]["document_id"] == "d1"
