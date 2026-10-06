"""Legacy Scout used only by the old pipeline/explicit legacy fake CLI.

This path treats snippets as evidence and is NOT a mono-v1 research worker.
Formal original-range and per-spec coverage boundaries live in ``originals``
and ``coverage``; the formal worker must use those, not call ``research`` here.
The legacy chain is retained until its callers migrate (T060).
"""

from __future__ import annotations

from typing import Any

from domain.ports import LLMPort, RetrievalPort, SearchPort, SearchResult
from domain.research.agents.base import call_llm, parse_json
from domain.research.ids import stable_id


async def research(
    section: dict[str, Any],
    search: SearchPort,
    retrieval: RetrievalPort,
    llm: LLMPort,
    kb_id: str = "default",
) -> dict[str, Any]:
    """Gather evidence, register sources, and extract claims for one section.

    Args:
        section: A SectionPlan dict with objective and sub_questions.
        search: SearchPort (paper/web).
        retrieval: RetrievalPort (local KB).
        llm: LLMPort (claim extraction).
        kb_id: The knowledge base id for local retrieval.

    Returns:
        {"evidence": {evidence_id: Evidence}, "sources": {source_id: SourceRecord},
         "claims": {claim_id: Claim}, "claim_evidence_links": [ClaimEvidenceLink]}.
    """
    evidence: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}

    for query in section.get("sub_questions", [section.get("objective", "")]):
        for result in await search.search(query):
            sources[result.source_id] = _register_source(result)
            evidence_id = stable_id("ev", result.source_id, "snippet", result.snippet)
            evidence[evidence_id] = {
                "evidence_id": evidence_id,
                "source_id": result.source_id,
                "evidence_type": result.source_type,
                "location": "snippet",
                "quote_or_raw_content": result.snippet,
                "extraction_method": "search_snippet",
            }

    for chunk in await retrieval.retrieve(section.get("objective", ""), kb_id, 5):
        evidence_id = stable_id("ev", chunk.chunk_id, chunk.metadata.get("page", "?"), chunk.text)
        evidence[evidence_id] = {
            "evidence_id": evidence_id,
            "source_id": chunk.chunk_id,
            "evidence_type": "local",
            "location": chunk.metadata.get("page", "?"),
            "quote_or_raw_content": chunk.text,
            "extraction_method": "local_retrieval",
        }

    claims, links = await _extract_claims(llm, evidence)
    observations = await _extract_observations(llm, evidence)
    coverage = _section_coverage(section, claims, links)
    return {
        "evidence": evidence,
        "sources": sources,
        "claims": claims,
        "claim_evidence_links": links,
        "quantitative_observations": observations,
        "section_coverage": coverage,
    }


def _section_coverage(
    section: dict[str, Any],
    claims: dict[str, dict[str, Any]],
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute per-section coverage: which claims are covered vs gap."""
    covered = {link["claim_id"] for link in links}
    gaps = [cid for cid in claims if cid not in covered]
    return {
        "section_id": section.get("section_id", ""),
        "covered_claim_ids": sorted(covered),
        "gaps": [{"claim_id": cid, "reason": "no_evidence"} for cid in gaps],
    }


async def gap_fill(
    section: dict[str, Any],
    claims: dict[str, dict[str, Any]],
    coverage: dict[str, Any],
    search: SearchPort,
) -> dict[str, Any]:
    """Fill coverage gaps with targeted searches (FR-007).

    For each uncovered claim, search its claim text (plus the section objective)
    and return the new evidence and sources.
    """
    gaps = coverage.get("gaps", [])
    if not gaps:
        return {"evidence": {}, "sources": {}}
    evidence: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    for gap in gaps:
        claim = claims.get(gap.get("claim_id", ""), {})
        query = f"{section.get('objective', '')} {claim.get('text', '')}".strip()
        if not query:
            continue
        ev, src = await _search_for(query, search, "gap_fill")
        evidence.update(ev)
        sources.update(src)
    return {"evidence": evidence, "sources": sources}


async def citation_trace(sources: dict[str, dict[str, Any]], search: SearchPort) -> dict[str, Any]:
    """Trace secondary sources to primary ones (FR-007)."""
    evidence: dict[str, dict[str, Any]] = {}
    new_sources: dict[str, dict[str, Any]] = {}
    for src in sources.values():
        if src.get("source_tier") != "secondary":
            continue
        query = f"{src.get('title', '')} original paper".strip()
        if not query:
            continue
        ev, srcs = await _search_for(query, search, "citation_trace")
        evidence.update(ev)
        new_sources.update(srcs)
    return {"evidence": evidence, "sources": new_sources}


async def _search_for(
    query: str, search: SearchPort, method: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Search one query and build id-keyed evidence + registered sources."""
    evidence: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    for result in await search.search(query):
        sources[result.source_id] = _register_source(result)
        evidence_id = stable_id("ev", result.source_id, "snippet", result.snippet)
        evidence[evidence_id] = {
            "evidence_id": evidence_id,
            "source_id": result.source_id,
            "evidence_type": result.source_type,
            "location": "snippet",
            "quote_or_raw_content": result.snippet,
            "extraction_method": method,
        }
    return evidence, sources


def _register_source(result: SearchResult) -> dict[str, Any]:
    """Legacy projection only; candidate metadata cannot prove peer review."""
    return {
        "source_id": result.source_id,
        "source_type": result.source_type,
        "title": result.title,
        "authors_or_publisher": "; ".join(result.authors_or_publisher),
        "published_at": result.published_at,
        "version": result.version,
        "canonical_url": result.url,
        "provenance": result.provider,
        "source_tier": result.source_tier,
    }


async def _extract_claims(
    llm: LLMPort, evidence: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Extract research claims from evidence via the LLM, bound to their evidence."""
    if not evidence:
        return {}, []
    judgment = parse_json(await call_llm(llm, _claims_prompt(evidence)))
    claims: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []
    for item in judgment.get("claims", []):
        text = item.get("text", "")
        if not text:
            continue
        claim_id = stable_id("cl", text)
        claims[claim_id] = {
            "claim_id": claim_id,
            "text": text,
            "conditions": item.get("conditions", {}),
            "status": "open",
        }
        for evidence_id in item.get("evidence_ids", []):
            if evidence_id in evidence:
                links.append(
                    {"claim_id": claim_id, "evidence_id": evidence_id, "relation": "supports"}
                )
    return claims, links


def _claims_prompt(evidence: dict[str, dict[str, Any]]) -> str:
    """Build the claim-extraction prompt from the gathered evidence snippets."""
    snippets = "\n".join(
        f"- [{ev_id}] {ev.get('location', '?')}: {ev.get('quote_or_raw_content', '')[:300]}"
        for ev_id, ev in list(evidence.items())[:20]
    )
    return (
        "You are a research evidence analyst. Given evidence snippets from a "
        "cybersecurity research search, extract the research claims each snippet "
        "supports. Respond with JSON only:\n"
        '{"claims": [{"text": "...", "conditions": {"dataset": "...", "protocol": "...", '
        '"metric": "..."}, "evidence_ids": ["ev-..."]}]}\n\n'
        f"Evidence:\n{snippets}\n"
    )


async def _extract_observations(
    llm: LLMPort, evidence: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Extract quantitative observations (result-table cells) from evidence."""
    if not evidence:
        return {}
    judgment = parse_json(await call_llm(llm, _observations_prompt(evidence)))
    observations: dict[str, dict[str, Any]] = {}
    for item in judgment.get("observations", []):
        evidence_id = item.get("evidence_id", "")
        if evidence_id not in evidence:
            continue
        row_key = item.get("row_key", "")
        column_key = item.get("column_key", "")
        observation_id = stable_id("obs", evidence_id, row_key, column_key)
        observations[observation_id] = {
            "observation_id": observation_id,
            "evidence_id": evidence_id,
            "kind": item.get("kind", ""),
            "row_key": row_key,
            "column_key": column_key,
            "value": item.get("value", ""),
            "uncertainty": item.get("uncertainty", ""),
            "statistic": item.get("statistic", ""),
        }
    return observations


def _observations_prompt(evidence: dict[str, dict[str, Any]]) -> str:
    """Build the quantitative-observation extraction prompt from evidence."""
    snippets = "\n".join(
        f"- [{ev_id}] {ev.get('location', '?')}: {ev.get('quote_or_raw_content', '')[:300]}"
        for ev_id, ev in list(evidence.items())[:20]
    )
    return (
        "You are a research data extractor. Given evidence snippets containing "
        "experimental results, extract quantitative observations (result-table "
        "cells) as structured rows. Respond with JSON only:\n"
        '{"observations": [{"row_key": "...", "column_key": "...", "value": "...", '
        '"uncertainty": "...", "statistic": "...", "evidence_id": "ev-..."}]}\n\n'
        f"Evidence:\n{snippets}\n"
    )
