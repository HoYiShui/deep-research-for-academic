"""Writer agent: write draft sections with evidence bindings.

Generates per-section prose (via the LLM) from claims and evidence, binding each
key conclusion to its cited evidence through program-level DraftClaimBinding
(not just natural-language footnotes). Emits the unified report skeleton.
"""
from __future__ import annotations

from typing import Any

from domain.ports import LLMPort
from domain.research.agents.base import call_llm, parse_json
from domain.research.ids import stable_id


async def write_report(
    section_plans: list[dict[str, Any]],
    claims: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    metrics: dict[str, dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
    brief: dict[str, Any],
    llm: LLMPort,
) -> dict[str, Any]:
    """Write the full report: per-section prose + program-level bindings.

    Args:
        section_plans: SectionPlan list.
        claims: id-keyed Claim dict.
        evidence: id-keyed Evidence dict.
        metrics: id-keyed ComparableMetric dict.
        artifacts: id-keyed AnalysisArtifact dict.
        brief: The frozen ResearchBrief.
        llm: LLMPort (prose generation).

    Returns:
        {"draft_sections": {section_id: DraftSection},
         "draft_claim_bindings": [DraftClaimBinding], "final_report": dict}.
    """
    draft_sections: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    for section in section_plans:
        section_id = section.get("section_id", f"s{len(draft_sections)}")
        result = parse_json(await call_llm(llm, _section_prompt(section, claims, evidence)))
        draft_sections[section_id] = {
            "section_id": section_id,
            "title": section.get("title", section.get("objective", "")),
            "content": result.get("content", ""),
        }
        bindings.extend(_bindings(section_id, result.get("bindings", [])))
    task_section = await _task_specific_section(
        brief.get("task_type", ""), brief, claims, evidence, metrics, artifacts, llm
    )
    if task_section:
        draft_sections["section_3"] = {
            "section_id": "section_3",
            "title": "核心分析",
            "content": task_section,
        }
    return {
        "draft_sections": draft_sections,
        "draft_claim_bindings": bindings,
        "final_report": {
            "title": brief.get("research_object", "Research Report"),
            "sections": draft_sections,
        },
    }


async def revise_report(
    section_plans: list[dict[str, Any]],
    claims: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    metrics: dict[str, dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
    brief: dict[str, Any],
    previous_sections: dict[str, dict[str, Any]],
    feedback: list[dict[str, Any]],
    llm: LLMPort,
) -> dict[str, Any]:
    """Revise sections flagged in feedback; keep others from the previous draft.

    Args:
        previous_sections: The prior draft_sections (non-affected kept as-is).
        feedback: CriticFeedback list; only sections named in target_id are rewritten.
    """
    affected = {f.get("target_id") for f in feedback}
    draft_sections = {k: dict(v) for k, v in previous_sections.items()}
    bindings: list[dict[str, Any]] = []
    for section in section_plans:
        section_id = section.get("section_id", "")
        if section_id not in affected:
            continue
        section_feedback = [f for f in feedback if f.get("target_id") == section_id]
        result = parse_json(
            await call_llm(llm, _section_prompt(section, claims, evidence, section_feedback))
        )
        draft_sections[section_id] = {
            "section_id": section_id,
            "title": section.get("title", section.get("objective", "")),
            "content": result.get("content", ""),
        }
        bindings.extend(_bindings(section_id, result.get("bindings", [])))
    return {
        "draft_sections": draft_sections,
        "draft_claim_bindings": bindings,
        "final_report": {
            "title": brief.get("research_object", "Research Report"),
            "sections": draft_sections,
        },
    }


def _bindings(section_id: str, raw_bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw LLM bindings into DraftClaimBinding dicts."""
    out: list[dict[str, Any]] = []
    for b in raw_bindings:
        out.append(
            {
                "section_id": section_id,
                "statement_id": b.get("statement_id", stable_id("st", section_id)),
                "claim_ids": b.get("claim_ids", []),
                "cited_evidence_ids": b.get("cited_evidence_ids", []),
                "artifact_ids": b.get("artifact_ids", []),
            }
        )
    return out


# Task-specific section-3 instructions (aligned with docs/contracts/report-skeleton.md).
_TASK_SECTION_INSTRUCTIONS = {
    "idea_exploration": "candidate research problems with verifiable hypotheses, "
    "required data/resources, novelty risk, and feasibility (a markdown table)",
    "method_differentiation": "a nearest-work comparison matrix (input, mechanism, "
    "output, solved limitations, open problems)",
    "evaluation_design": "a protocol-metric-conclusion mapping (claim, protocol, "
    "baseline, metric, supported and unsupported conclusions)",
}


async def _task_specific_section(
    task_type: str,
    brief: dict[str, Any],
    claims: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    metrics: dict[str, dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
    llm: LLMPort,
) -> str:
    """Generate the task-specific section 3 (report-skeleton.md) via the LLM."""
    instruction = _TASK_SECTION_INSTRUCTIONS.get(task_type)
    if instruction is None:
        return ""
    raw = await call_llm(llm, _task_section_prompt(instruction, brief, claims))
    return parse_json(raw).get("content", "")


def _task_section_prompt(
    instruction: str, brief: dict[str, Any], claims: dict[str, dict[str, Any]]
) -> str:
    """Build the task-specific section-3 prompt."""
    claim_lines = "\n".join(
        f"- [{cid}] {c.get('text', '')}" for cid, c in list(claims.items())[:15]
    )
    return (
        "You are a research report writer. Write the core-analysis section (section 3) "
        "of a cybersecurity research report. This section must present: "
        f"{instruction}. Respond with JSON only:\n"
        '{"content": "..."}\n\n'
        f"Research object: {brief.get('research_object', '')}\n"
        f"Decision goal: {brief.get('decision_goal', '')}\n"
        f"Claims:\n{claim_lines}\n"
    )


def _section_prompt(
    section: dict[str, Any],
    claims: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    feedback: list[dict[str, Any]] | None = None,
) -> str:
    """Build the per-section prose-generation prompt."""
    claim_lines = "\n".join(f"- [{cid}] {c.get('text', '')}" for cid, c in list(claims.items())[:15])
    evidence_lines = "\n".join(
        f"- [{eid}] {e.get('location', '?')}: {e.get('quote_or_raw_content', '')[:200]}"
        for eid, e in list(evidence.items())[:20]
    )
    feedback_line = ""
    if feedback:
        feedback_line = "Feedback to address:\n" + "\n".join(
            f"- {f.get('issue_type')}: {f.get('description', '')}" for f in feedback
        ) + "\n"
    return (
        "You are a research report writer. Write a section of a cybersecurity "
        "research report. Every key conclusion must cite its evidence id. Respond "
        "with JSON only:\n"
        '{"content": "...", "bindings": [{"statement_id": "...", "claim_ids": ["cl-..."], '
        '"cited_evidence_ids": ["ev-..."], "artifact_ids": []}]}\n\n'
        f"Section objective: {section.get('objective', '')}\n"
        f"Claims:\n{claim_lines}\n\nEvidence:\n{evidence_lines}\n{feedback_line}"
    )
