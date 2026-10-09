"""Integration tests for the KB-search slice (T039).

Verifies the RetrievalPort composition (embed -> hybrid -> rerank), Milvus
degrade-to-empty with a non-fatal gap, the orchestrator's milvus_unavailable
event, and the /knowledge-base/search endpoint.
"""

from unittest.mock import patch

import pytest

from application.orchestrator import Orchestrator
from application.sse import EventBus
from domain.ports import Chunk, Embedding
from infrastructure.fake import FakeExecution, FakeLLM, FakeSearch, FakeStateStore
from infrastructure.retrieval.local import LocalRetrieval
from infrastructure.storage.memory import InMemoryCancel


class _FakeEmbedding:
    async def embed(self, text: str) -> Embedding:
        return Embedding(dense=[1.0], sparse={})


class _FakeVector:
    async def hybrid_search(self, kb_id: str, embedding: Embedding, top_k: int) -> list[Chunk]:
        return [Chunk("c1", "text a", 0.5, {}), Chunk("c2", "text b", 0.4, {})]


class _FakeReranker:
    async def rerank(self, query: str, candidates: list[Chunk], top_k: int) -> list[Chunk]:
        return candidates[:top_k]


class _FailingVector:
    async def hybrid_search(self, kb_id: str, embedding: Embedding, top_k: int) -> list[Chunk]:
        raise RuntimeError("milvus down")


@pytest.mark.asyncio
async def test_local_retrieval_composes_embed_hybrid_rerank() -> None:
    retrieval = LocalRetrieval(_FakeEmbedding(), _FakeVector(), _FakeReranker())
    chunks = await retrieval.retrieve("q", "kb", 2)
    assert [c.chunk_id for c in chunks] == ["c1", "c2"]
    assert retrieval.take_gaps() == []


@pytest.mark.asyncio
async def test_milvus_unavailable_degrades_to_empty() -> None:
    retrieval = LocalRetrieval(_FakeEmbedding(), _FailingVector(), _FakeReranker())
    chunks = await retrieval.retrieve("q", "kb", 2)
    assert chunks == []
    gaps = retrieval.take_gaps()
    assert gaps and gaps[0]["reason"] == "unavailable"


def _drain(bus: EventBus, session_id: str) -> list:
    events = []
    queue = bus.queue(session_id)
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.mark.asyncio
async def test_orchestrator_emits_milvus_unavailable_and_completes() -> None:
    bus = EventBus()
    store = FakeStateStore()
    llm = FakeLLM(
        response='{"section_plans": [{"section_id": "s1", "objective": "o", '
        '"sub_questions": ["q1"]}]}'
    )
    retrieval = LocalRetrieval(_FakeEmbedding(), _FailingVector(), _FakeReranker())
    orchestrator = Orchestrator(
        bus, InMemoryCancel(), store, llm, FakeSearch(), retrieval, FakeExecution()
    )
    await orchestrator.run("kb-s1", {"task_type": "idea_exploration"})

    events = _drain(bus, "kb-s1")
    errors = [e for e in events if type(e).__name__ == "ErrorEvent"]
    done = [e for e in events if type(e).__name__ == "DoneEvent"]
    assert any(e.code == "milvus_unavailable" for e in errors)
    assert any(e.status == "completed" for e in done)


def test_legacy_kb_search_endpoint_is_retired() -> None:
    import jwt
    from fastapi.testclient import TestClient

    from application.auth_service import AuthService
    from infrastructure.storage.memory import InMemoryUserStore
    from interface.main import app

    secret = "a" * 32
    token = jwt.encode({"sub": "user-1"}, secret, algorithm="HS256")

    class _Retrieval:
        async def retrieve(self, query: str, kb_id: str, top_k: int) -> list[Chunk]:
            return [Chunk("c1", "hello", 0.9, {})]

    class _Container:
        retrieval = _Retrieval()
        auth = AuthService(InMemoryUserStore(), secret=secret)

    container = _Container()
    with (
        patch("interface.router.knowledge_base.get_container", return_value=container),
        patch("interface.deps.get_container", return_value=container),
    ):
        client = TestClient(app)
        resp = client.post(
            "/knowledge-base/search",
            json={"query": "q", "kb_id": "kb", "top_k": 5},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
