"""Architect agent: clarify and plan responsibilities.

clarify produces a judgment (missing fields + questions), never a status;
plan turns a frozen brief into section plans. Both are pure workers: they call
the LLM and return structured results, leaving control flow to the caller.
"""
from __future__ import annotations

from typing import Any

from domain.ports import LLMPort
from domain.research.agents.base import call_llm, parse_json


async def clarify(llm: LLMPort, brief_draft: dict, answer: str) -> dict[str, Any]:
    """Produce a clarify judgment for one round.

    Args:
        llm: The LLM port.
        brief_draft: The current brief draft.
        answer: The user's latest answer (or the original query).

    Returns:
        {"missing_fields", "questions", "brief_patch", "assumptions"}.
        Never includes "status" -- the caller decides that via decide_status.
    """
    raw = await call_llm(llm, _clarify_prompt(brief_draft, answer))
    judgment = parse_json(raw)
    return {
        "missing_fields": judgment.get("missing_fields", []),
        "questions": judgment.get("questions", []),
        "brief_patch": judgment.get("brief_patch", {}),
        "assumptions": judgment.get("assumptions", []),
    }


async def plan(llm: LLMPort, brief: dict) -> list[dict[str, Any]]:
    """Generate section plans from a frozen brief.

    Args:
        llm: The LLM port.
        brief: The frozen ResearchBrief.

    Returns:
        A list of SectionPlan dicts.
    """
    raw = await call_llm(llm, _plan_prompt(brief))
    return parse_json(raw).get("section_plans", [])


def _clarify_prompt(brief_draft: dict, answer: str) -> str:
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
