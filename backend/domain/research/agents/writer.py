"""LeadWriter agent: write draft sections with evidence bindings."""

from __future__ import annotations

from typing import Any


def write_report(sections: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Write draft sections and bind each statement to cited evidence.

    Args:
        sections: SectionPlan list.
        evidence: Evidence list.

    Returns:
        {"draft_sections": [...], "draft_claim_bindings": [...]}, where every
        binding cites at least one evidence source_id.
    """
    draft_sections = []
    bindings = []
    for i, section in enumerate(sections):
        section_id = section.get("section_id", f"s{i}")
        draft_sections.append({"section_id": section_id, "content": f"Section {section_id}"})
        for j, ev in enumerate(evidence):
            bindings.append(
                {
                    "section_id": section_id,
                    "statement_id": f"st-{i}-{j}",
                    "cited_evidence_ids": [ev["source_id"]],
                }
            )
    return {"draft_sections": draft_sections, "draft_claim_bindings": bindings}
