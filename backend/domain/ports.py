"""Domain ports: abstract interfaces consumed by agents.

Agents depend on these interfaces, not concrete implementations; adapters live
in infrastructure. Read/write asymmetry: reads go through RetrievalPort
(embed -> hybrid -> rerank); writes (ingest) use EmbeddingPort + VectorStorePort
directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SearchResult:
    """A candidate document returned by an external search source (paper/web)."""

    source_id: str
    source_type: str  # "paper" | "web"
    title: str
    snippet: str
    url: str = ""


@dataclass
class Embedding:
    """A BGE-M3 dual vector: dense for semantics, sparse for exact terms."""

    dense: list[float]
    sparse: dict[str, float] = field(default_factory=dict)


@dataclass
class Chunk:
    """A chunk retrieved from the local knowledge base."""

    chunk_id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


class LLMPort(Protocol):
    """Call the LLM (Anthropic-compatible API)."""

    async def complete(self, prompt: str) -> str: ...


class SearchPort(Protocol):
    """Search an external source (paper/web) and return candidates."""

    async def search(self, query: str) -> list[SearchResult]: ...


class RetrievalPort(Protocol):
    """Unified local-KB retrieval: embed -> hybrid -> rerank."""

    async def retrieve(self, query: str, kb_id: str, top_k: int) -> list[Chunk]: ...


class EmbeddingPort(Protocol):
    """Embed text into a dual vector (dense + sparse)."""

    async def embed(self, text: str) -> Embedding: ...


class VectorStorePort(Protocol):
    """Store and search vectors (hybrid search + insert)."""

    async def hybrid_search(self, kb_id: str, embedding: Embedding, top_k: int) -> list[Chunk]: ...

    async def insert(self, kb_id: str, chunk_id: str, embedding: Embedding, metadata: dict) -> None: ...


class RerankPort(Protocol):
    """Rerank candidates with a cross-encoder."""

    async def rerank(self, query: str, candidates: list[Chunk], top_k: int) -> list[Chunk]: ...


class ContentStorePort(Protocol):
    """Read locally stored content (MinIO)."""

    async def get(self, chunk_id: str) -> str: ...


class FetchPort(Protocol):
    """Fetch external full text (arXiv/web) on a cache miss."""

    async def fetch(self, source_type: str, doc_ref: str) -> str: ...


class EventSink(Protocol):
    """Emit an event (fire-and-forget into the in-process queue)."""

    def emit(self, event: Any) -> None: ...


class CodeExecutionPort(Protocol):
    """Execute code in isolation (sandbox)."""

    async def execute(self, code: str, input_data: dict, timeout_s: int = 30) -> dict: ...
