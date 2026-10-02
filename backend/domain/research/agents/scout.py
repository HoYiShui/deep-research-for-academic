"""DeepScout agent: multi-source retrieval and evidence dedup.

Gathers evidence from paper/web (SearchPort) and the local KB (RetrievalPort),
then deduplicates by (source_id, location, quote).
"""
from __future__ import annotations

from typing import Any

from domain.ports import RetrievalPort, SearchPort


async def research(
    section: dict[str, Any],
    search: SearchPort,
    retrieval: RetrievalPort,
    kb_id: str = "default",
) -> dict[str, Any]:
    """Gather evidence for one section.

    Args:
        section: A SectionPlan dict with objective and sub_questions.
        search: SearchPort (paper/web).
        retrieval: RetrievalPort (local KB).
        kb_id: The knowledge base id for local retrieval.

    Returns:
        {"evidence": [...]} deduplicated by (source_id, location, quote).
    """
    evidence: list[dict[str, Any]] = []
    for query in section.get("sub_questions", [section.get("objective", "")]):
        for result in await search.search(query):
            evidence.append(
                {
                    "source_id": result.source_id,
                    "source_type": result.source_type,
                    "location": "snippet",
                    "quote": result.snippet,
                }
            )
    for chunk in await retrieval.retrieve(section.get("objective", ""), kb_id, 5):
        evidence.append(
            {
                "source_id": chunk.chunk_id,
                "source_type": "local",
                "location": chunk.metadata.get("page", "?"),
                "quote": chunk.text,
            }
        )
    return {"evidence": _dedup(evidence)}


def _dedup(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicates by (source_id, location, quote); keep multi-source."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for item in evidence:
        key = (item["source_id"], item["location"], item["quote"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
