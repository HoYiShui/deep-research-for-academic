"""Architect agent: clarify and plan responsibilities.

clarify produces a judgment (missing fields + questions), never a status;
plan turns a frozen brief into section plans. Both are pure workers: they call
the LLM and return structured results, leaving control flow to the caller.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import field_validator

from domain.ports import LLMPort
from domain.research.agents.base import call_llm, parse_json
from domain.research.agents.prompts import (
    CLARIFY_FEW_SHOTS,
    CLARIFY_PROMPT_TEMPLATE,
    PLAN_PROMPT_TEMPLATE,
    PLAN_TASK_DIMENSIONS,
)
from domain.research.agents.structured import complete as _structured
from domain.research.facts import SectionPlan
from domain.research.models import (
    ClarifyAssessment,
    PartialResearchBrief,
    Record,
    ResearchBrief,
    SourceSelection,
)
from domain.research.phase_contracts import validate_plans


async def clarify(
    llm: LLMPort,
    *,
    draft: PartialResearchBrief,
    query: str,
    answer: str,
    source_selection: SourceSelection,
    pending_questions: list[str],
    history: list[dict],
    timeout_s: float = 60,
) -> ClarifyAssessment:
    """Strict mono worker: no status, storage, default success or silent repair."""
    draft = PartialResearchBrief.model_validate(draft)
    selection = SourceSelection.model_validate(source_selection)
    if not isinstance(query, str) or not query.strip() or len(query) > 16000:
        raise ValueError("Clarify query must contain 1..16000 characters")
    if not isinstance(answer, str) or len(answer) > 16000:
        raise ValueError("Clarify answer exceeds its bound")
    if len(pending_questions) > 2 or any(
        not isinstance(item, str) or len(item) > 8000 for item in pending_questions
    ):
        raise ValueError("Pending questions exceed their bound")
    # Service supplies persisted history; only a bounded tail enters the model.
    tail = [{"role": item["role"], "content": item["content"][-8000:]} for item in history[-8:]]
    context = json.dumps(
        {
            "query": query,
            "draft": draft.model_dump(),
            "answer": answer,
            "source_selection": selection.model_dump(mode="json"),
            "pending_questions": pending_questions,
            "history": tail,
        },
        ensure_ascii=False,
    )
    prompt = CLARIFY_PROMPT_TEMPLATE.format(context=context, examples=CLARIFY_FEW_SHOTS)
    return await _structured(
        llm, prompt, ClarifyAssessment, operation="clarify", timeout_s=timeout_s
    )


async def legacy_clarify(llm: LLMPort, brief_draft: dict, answer: str) -> dict[str, Any]:
    """Pre-mono compatibility only, removed at the service composition cutover.

    Args:
        llm: The LLM port.
        brief_draft: The current brief draft.
        answer: The user's latest answer (or the original query).

    Returns:
        {"missing_fields", "questions", "brief_patch", "assumptions"}.
        Never includes "status" -- the caller decides that via decide_status.
    """
    raw = await call_llm(llm, _legacy_clarify_prompt(brief_draft, answer))
    judgment = parse_json(raw)
    return {
        "missing_fields": judgment.get("missing_fields", []),
        "questions": judgment.get("questions", []),
        "brief_patch": judgment.get("brief_patch", {}),
        "assumptions": judgment.get("assumptions", []),
    }


async def legacy_plan(llm: LLMPort, brief: dict) -> list[dict[str, Any]]:
    """Generate section plans from a frozen brief.

    Args:
        llm: The LLM port.
        brief: The frozen ResearchBrief.

    Returns:
        A list of SectionPlan dicts.
    """
    raw = await call_llm(llm, _plan_prompt(brief))
    return parse_json(raw).get("section_plans", [])


class PlanOutput(Record):
    section_plans: list[SectionPlan]

    @field_validator("section_plans")
    @classmethod
    def complete_plan(cls, value):
        return validate_plans(value)


async def plan(
    llm: LLMPort, brief: ResearchBrief, *, source_selection=None, timeout_s=60
) -> list[SectionPlan]:
    """Canonical frozen-brief worker. Invalid/empty plans raise, never return []."""
    brief = ResearchBrief.model_validate(brief)
    sources = SourceSelection.model_validate(
        SourceSelection() if source_selection is None else source_selection
    )
    context = json.dumps(
        {
            "research_brief": brief.model_dump(mode="json"),
            "source_selection": sources.model_dump(mode="json"),
        },
        ensure_ascii=False,
    )
    prompt = PLAN_PROMPT_TEMPLATE.format(
        task_dimensions=PLAN_TASK_DIMENSIONS[brief.task_type],
        output_schema=json.dumps(PlanOutput.model_json_schema(), ensure_ascii=False),
        context=context,
    )
    output = await _structured(
        llm, prompt, PlanOutput, operation="plan", timeout_s=timeout_s, max_chars=128000
    )
    return output.section_plans


def _legacy_clarify_prompt(brief_draft: dict, answer: str) -> str:
    return (
        "You are a research planning assistant. Given a research brief draft and "
        "the user's latest answer, extract task_type, decision_goal, research_object, "
        "and deliverable into brief_patch (fill them whenever they can be inferred, "
        "even if not explicitly stated). Only list fields still genuinely missing in "
        "missing_fields, and ask clarifying questions for those. Respond with JSON only:\n"
        '{"missing_fields": ["..."], "questions": ["..."], '
        '"brief_patch": {"task_type": "...", "decision_goal": "...", '
        '"research_object": "...", "deliverable": "..."}, "assumptions": ["..."]}\n\n'
        f"Brief draft: {brief_draft}\nUser answer: {answer}\n"
    )


def _plan_prompt(brief: dict) -> str:
    return (
        "You are a research planning assistant. Given a frozen research brief, "
        "generate a section plan. Respond with JSON only:\n"
        '{"section_plans": [{"section_id": "...", "objective": "...", '
        '"sub_questions": ["..."], "retrieval_anchors": ["..."], '
        '"evidence_requirements": ["..."]}]}\n\n'
        f"Brief: {brief}\n"
    )
