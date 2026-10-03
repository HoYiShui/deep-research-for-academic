"""Scout agent: multi-source retrieval and evidence dedup.

Gathers evidence from paper/web (SearchPort) and the local KB (RetrievalPort),
then deduplicates by (source_id, location, quote) and keys by evidence_id.
"""
from __future__ import annotations

from typing import Any

from domain.ports import RetrievalPort, SearchPort
from domain.research.ids import stable_id


async def research(
    section: dict[str, Any],
    search: SearchPort,
    retrieval: RetrievalPort,
    kb_id: str = "default",
) -> dict[str, Any]:
    """Gather evidence for one section, returning id-keyed evidence.

    Args:
        section: A SectionPlan dict with objective and sub_questions.
        search: SearchPort (paper/web).
        retrieval: RetrievalPort (local KB).
        kb_id: The knowledge base id for local retrieval.

    Returns:
        {"evidence": {evidence_id: Evidence}} deduplicated by (source_id,
        location, quote).
    """
    raw: list[dict[str, Any]] = []
    for query in section.get("sub_questions", [section.get("objective", "")]):
        for result in await search.search(query):
            raw.append(
                {
                    "source_id": result.source_id,
                    "evidence_type": result.source_type,
                    "location": "snippet",
                    "quote_or_raw_content": result.snippet,
                    "extraction_method": "search_snippet",
                }
            )
    for chunk in await retrieval.retrieve(section.get("objective", ""), kb_id, 5):
        raw.append(
            {
                "source_id": chunk.chunk_id,
                "evidence_type": "local",
                "location": chunk.metadata.get("page", "?"),
                "quote_or_raw_content": chunk.text,
                "extraction_method": "local_retrieval",
            }
        )
    evidence: dict[str, dict[str, Any]] = {}
    for item in _dedup(raw):
        evidence_id = stable_id("ev", item["source_id"], item["location"], item["quote_or_raw_content"])
        evidence[evidence_id] = {"evidence_id": evidence_id, **item}
    return {"evidence": evidence}


def _dedup(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicates by (source_id, location, quote); keep multi-source."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for item in raw:
        key = (item["source_id"], item["location"], item["quote_or_raw_content"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
