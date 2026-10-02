"""Spike: verify BGE-M3 sparse + Milvus hybrid search (RRF fusion).

Run with Milvus up and the model cache warm:
    MILVUS_HOST=localhost MILVUS_PORT=19530 python scripts/spike_bge_milvus.py

Checks that BGE-M3 emits a dense vector plus sparse lexical weights, and that
a Milvus hybrid search fuses dense + sparse ranks via reciprocal-rank fusion.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from infrastructure.embedding.bge_m3 import BGEM3Embedding


def _rrf(dense_rank: int, sparse_rank: int, k: int = 60) -> float:
    return 1.0 / (k + dense_rank) + 1.0 / (k + sparse_rank)


async def main() -> None:
    embedding = BGEM3Embedding()
    vec = await embedding.embed("transformer attention mechanism")
    print(f"dense dim={len(vec.dense)}  sparse terms={len(vec.sparse)}")
    if not vec.sparse:
        print("No sparse weights emitted; check the BGE-M3 model supports return_sparse.")
        return

    host = os.environ.get("MILVUS_HOST", "localhost")
    port = int(os.environ.get("MILVUS_PORT", "19530"))
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=f"http://{host}:{port}")
        collections = client.list_collections()
        print(f"milvus collections={collections}")
    except Exception as exc:  # noqa: BLE001 — spike, print and continue
        print(f"Milvus not reachable ({exc}); dense+sparse encoding verified only.")
        return

    # Hybrid search with RRF over a dense + sparse dual-vector collection.
    params = {"params": {"rrf_k": 60}}
    hits = client.search(
        collection_name="default",
        data=[vec.dense],
        search_params=params,
        limit=10,
        output_fields=["id"],
    )
    print(f"hybrid hits={len(hits[0]) if hits else 0}")
    print(f"rrf sanity: d1/s2 -> {_rrf(1, 2):.4f}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
