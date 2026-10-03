"""Writer agent: write draft sections with evidence bindings."""

from __future__ import annotations

from typing import Any

from domain.research.ids import stable_id


def write_report(sections: list[dict[str, Any]], evidence: dict[str, dict]) -> dict[str, Any]:
    """Write draft sections and bind each statement to cited evidence.

    Args:
        sections: SectionPlan list.
        evidence: id-keyed Evidence dict.

    Returns:
        {"draft_sections": {section_id: DraftSection},
         "draft_claim_bindings": [DraftClaimBinding]}, where every binding
        cites at least one evidence_id.
    """
    draft_sections: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    evidence_ids = list(evidence.keys())
    for i, section in enumerate(sections):
        section_id = section.get("section_id", f"s{i}")
        draft_sections[section_id] = {
            "section_id": section_id,
            "title": section.get("title", ""),
            "content": f"Section {section_id}",
        }
        for j, evidence_id in enumerate(evidence_ids):
            bindings.append(
                {
                    "section_id": section_id,
                    "statement_id": stable_id("st", section_id, str(j)),
                    "claim_ids": [],
                    "cited_evidence_ids": [evidence_id],
                    "artifact_ids": [],
                }
            )
    return {"draft_sections": draft_sections, "draft_claim_bindings": bindings}
