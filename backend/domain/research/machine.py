"""Pure policy layer: deterministic transition rules for clarify and pipeline.

The LLM only produces judgments (data); this module applies policy (control
flow). assess_brief drives mono clarification; next_phase drives the pipeline happy
path; route_after_review drives the rework routing table.
"""

from __future__ import annotations

from typing import Any

from pydantic import StrictBool

from domain.research.facts import ReworkTarget
from domain.research.models import (
    BriefDecision,
    ClarifyAssessment,
    Nonnegative,
    PartialResearchBrief,
    Record,
    ResearchBrief,
    Text,
)
from domain.research.phase_contracts import PhaseInput, WorkerPhase, validate_plans
from domain.research.state import PipelineState

EXPLICIT_BRIEF_FIELDS = ("task_type", "decision_goal", "research_object", "deliverable")
SAFE_BRIEF_DEFAULTS = {
    "scope": "Limit research to selected authorized sources; do not assume unspecified datasets, periods or deployment settings.",
    "comparison_scope": "Compare only explicitly identified objects; do not invent baselines or rank incompatible results.",
    "claims_to_verify": "Derive hypotheses from the decision goal and verify them; hypotheses are not established facts.",
    "evidence_requirements": "Require locatable original evidence for key facts; record gaps rather than treating snippets as evidence.",
    "conclusion_boundary": "Conclusions cannot exceed the evidence; do not assert unverified superiority, causality, novelty or production applicability.",
}


def assess_brief(draft: PartialResearchBrief, assessment: ClarifyAssessment) -> BriefDecision:
    """The model suggests gaps; only code decides whether confirmation is safe."""
    draft = PartialResearchBrief.model_validate(draft)
    assessment = ClarifyAssessment.model_validate(assessment)
    merged = draft.model_dump() | assessment.brief_patch.model_dump()
    gaps = set(assessment.missing_fields) | set(assessment.field_reasons)
    disclosures = [merged.get("assumptions", ""), *assessment.assumptions]
    for name, default in SAFE_BRIEF_DEFAULTS.items():
        if name not in merged and name not in gaps:
            merged[name] = default
            disclosures.append(f"Conservative default ({name}): {default}")
    merged["assumptions"] = "\n".join(
        dict.fromkeys(
            line.strip() for item in disclosures for line in item.splitlines() if line.strip()
        )
    )
    gaps.update(name for name in EXPLICIT_BRIEF_FIELDS if name not in merged)
    gaps.update(name for name in ResearchBrief.model_fields if name not in merged)
    ordered = [name for name in ResearchBrief.model_fields if name in gaps]
    candidate = PartialResearchBrief.model_validate(merged)
    if not ordered:
        ResearchBrief.model_validate(candidate.model_dump())
        return BriefDecision(status="confirm", draft=candidate, missing_fields=[], questions=[])
    questions = assessment.questions or [
        f"请明确任务书中的 {name}，以便限定研究决策与结论。" for name in ordered[:2]
    ]
    return BriefDecision(status="ask", draft=candidate, missing_fields=ordered, questions=questions)


# Explicitly pre-mono policy, removed with the legacy composition cutover.
LEGACY_CRITICAL_BRIEF_FIELDS = {"decision_goal", "research_object", "deliverable"}

# Pipeline phase order (happy path).
_PHASE_ORDER = ["plan", "research", "analyze", "write", "review", "done"]

# Phase -> worker agent (pure policy; the orchestrator dispatches via this table).
WORKERS = {
    "plan": "architect",
    "research": "scout",
    "analyze": "data_analyst",
    "write": "writer",
    "review": "critic",
}

# Rework action priority: earliest phase wins when issues conflict.
_ACTION_PRIORITY = ["re_research", "re_analyze", "revise", "acknowledge_limit"]


def legacy_decide_status(missing_fields: list[str]) -> str:
    """Decide clarify status from missing fields.

    Args:
        missing_fields: Fields the LLM judged as missing from the brief.

    Returns:
        "ask" if any critical field is missing, else "ready" (conservative
        defaults are used for non-critical gaps).
    """
    if set(missing_fields) & LEGACY_CRITICAL_BRIEF_FIELDS:
        return "ask"
    return "ready"


def next_phase(state: dict) -> str:
    """Return the next pipeline phase for the given state.

    Args:
        state: A PipelineState dict; reads state["phase"].

    Returns:
        The next phase name, or "done" if already at the end.
    """
    current = state.get("phase", "plan")
    try:
        idx = _PHASE_ORDER.index(current)
    except ValueError:
        return "done"
    return _PHASE_ORDER[idx + 1] if idx + 1 < len(_PHASE_ORDER) else "done"


def _action_for(issue: dict[str, Any]) -> str:
    """Map one issue to its rework action.

    The action is determined by issue_type (what kind of issue), not severity;
    severity only gates whether to rework at all. Issue types use English
    identifiers: missing_source, comparability_violation, hallucination,
    overclaim, outdated, logic_error.

    Args:
        issue: A critic feedback dict with issue_type and optional fillable.

    Returns:
        The rework action for this issue.
    """
    issue_type = issue.get("issue_type", "")
    if issue_type == "missing_source":
        return "re_research" if issue.get("fillable", False) else "acknowledge_limit"
    if issue_type == "comparability_violation":
        return "re_analyze"
    if issue_type == "hallucination":
        # Retract the claim, then re-search for real evidence.
        return "re_research"
    if issue_type == "overclaim":
        return "revise"
    if issue_type in ("outdated", "logic_error"):
        return "re_research"
    return "revise"  # default fallback


def route_after_review(issues: list[dict[str, Any]]) -> str:
    """Route critic feedback to the next pipeline action.

    Args:
        issues: List of critic feedback, each with issue_type, severity, fillable.

    Returns:
        "done" if no critical/major issue remains, else the highest-priority
        rework action (re_research > re_analyze > revise > acknowledge_limit).
    """
    actionable = [i for i in issues if i.get("severity", "minor") != "minor"]
    if not actionable:
        return "done"
    actions = [_action_for(i) for i in actionable]
    return min(actions, key=_ACTION_PRIORITY.index)


def phase_after_review(action: str) -> str:
    """Map a review route action to the next pipeline phase.

    Policy-only: the LLM never drives control flow. This table maps the
    deterministic action from route_after_review back to a phase, closing the
    rework loop (re_research -> research, re_analyze -> analyze, revise ->
    write; acknowledge_limit folds into write so the report is re-emitted).

    Args:
        action: A rework action from route_after_review.

    Returns:
        The phase to transition into, or "done" for terminal actions.
    """
    return {
        "done": "done",
        "re_research": "research",
        "re_analyze": "analyze",
        "revise": "write",
        "acknowledge_limit": "write",
    }.get(action, "done")


TERMINAL_REASONS = {"rework_limit", "budget_exhausted", "deadline_exhausted"}


class PipelineDecision(Record):
    next_phase: WorkerPhase | None
    deliver: StrictBool
    rework_count: Nonnegative
    targets: list[ReworkTarget]
    withdraw_claim_ids: list[Text]
    stop_reason: Text | None


def _review_claim_ids(state, issue):
    if issue.target_type == "claim":
        return [issue.target_id] if issue.target_id in state.claims else []
    return sorted(
        {
            claim
            for binding in state.draft_claim_bindings
            if binding.section_id == issue.section_id
            and (issue.target_type != "statement" or binding.statement_id == issue.target_id)
            for claim in binding.claim_ids
        }
    )


def validate_research_coverage(state):
    expected = {plan.section_id for plan in state.section_plans}
    if set(state.section_coverage) != expected:
        raise ValueError("Research must record coverage or explicit gaps for every section")
    for plan in state.section_plans:
        coverage = state.section_coverage[plan.section_id]
        if set(coverage.claim_spec_ids) != {spec.spec_id for spec in plan.claim_specs}:
            raise ValueError("Research coverage omits planned claim specs")
        for spec in plan.claim_specs:
            claims = [
                state.claims[key]
                for key in coverage.claim_ids
                if spec.spec_id in state.claims[key].spec_ids
            ]
            supported = any(
                claim.status not in {"open", "insufficient"}
                and any(link.claim_id == claim.claim_id for link in state.claim_evidence_links)
                for claim in claims
            )
            gap = any(
                item.claim_spec_id == spec.spec_id or item.claim_id in {c.claim_id for c in claims}
                for item in coverage.gaps
            )
            if not supported and not gap:
                raise ValueError("Unverified claim spec requires an explicit gap")


def decide_pipeline(state: PipelineState) -> PipelineDecision:
    """Formal mono policy. Delivery is a candidate, never phase=done publication.

    State schema closes unknown phases/issues. This function performs no I/O,
    never upgrades a model verdict and never turns a rework cap into approval.
    """
    state = PipelineState.model_validate(state)
    metadata = state.run_metadata
    base = {
        "deliver": False,
        "rework_count": metadata.rework_count,
        "targets": metadata.rework_targets,
        "withdraw_claim_ids": [],
        "stop_reason": metadata.stop_reason,
    }
    if state.phase == "done":
        raise ValueError("A published state has no pending pipeline transition")
    if state.phase != "review":
        validate_plans(state.section_plans)
        if state.phase == "research":
            validate_research_coverage(state)
        if state.phase == "analyze":
            required = {
                r.requirement_id for plan in state.section_plans for r in plan.analysis_requirements
            }
            if required - {group.requirement_id for group in state.comparison_sets.values()}:
                raise ValueError(
                    "Analysis must judge every planned requirement, including insufficiency"
                )
            if not required and not any(
                item.operation == "analysis_skipped" for item in metadata.degraded_sources
            ):
                raise ValueError(
                    "Analysis without quantitative requirements must record its skip reason"
                )
        next_value = {
            "plan": "research",
            "research": "analyze",
            "analyze": "write",
            "write": "review",
        }[state.phase]
        candidate = PipelineState.model_validate(state.model_dump() | {"phase": next_value})
        PhaseInput.from_state(candidate)  # Require the actual next worker preconditions.
        if metadata.stop_reason in TERMINAL_REASONS and next_value in {"research", "analyze"}:
            raise ValueError("Terminal contraction cannot launch retrieval or computation")
        return PipelineDecision(next_phase=next_value, **base)
    PhaseInput.from_state(state)
    if state.reviewed_draft_version != state.draft_version or state.review_verdict is None:
        raise ValueError("Review must judge the current complete draft")
    if any(
        issue.resolved
        and (not issue.resolution or issue.resolved_in_version != state.draft_version)
        for issue in state.critic_feedback
    ):
        raise ValueError("Resolved review issues require current-version verification")
    issues = [
        item for item in state.critic_feedback if not item.resolved and item.severity != "minor"
    ]
    if not issues:
        return PipelineDecision(next_phase=None, **(base | {"deliver": True, "targets": []}))
    if metadata.stop_reason in TERMINAL_REASONS:
        # A known source gap can remain in an explicitly limited auxiliary draft,
        # but never as an unsupported factual statement or an approved verdict.
        safe = state.review_verdict == "needs_more_work" and all(
            issue.issue_type == "missing_source"
            and not issue.fillable
            and not any(
                statement.kind == "factual"
                for statement in state.draft_sections[issue.section_id].statements
            )
            for issue in issues
        )
        if safe:
            return PipelineDecision(next_phase=None, **(base | {"deliver": True}))
        raise ValueError("Terminal contraction still has unsafe unresolved review issues")
    targets, withdrawn = [], set()
    for issue in issues:
        if issue.issue_type == "comparability_violation":
            action = "re_analyze"
        elif issue.issue_type == "overclaim":
            action = "revise"
        else:
            action = "re_research" if issue.fillable else "acknowledge_limit"
        claims = _review_claim_ids(state, issue)
        if issue.issue_type == "hallucination":
            withdrawn.update(claims)
        targets.append(
            ReworkTarget(
                issue_ids=[issue.issue_id],
                section_ids=[issue.section_id],
                claim_ids=claims,
                action=action,
                reason=issue.description,
            )
        )
    used, limits = metadata.budget_used, metadata.config.limits
    reason = (
        "deadline_exhausted"
        if used.elapsed_s >= limits.deadline_s
        else "budget_exhausted"
        if (
            used.llm_calls >= limits.llm_calls - limits.terminal_reserved_calls
            or used.tokens >= limits.tokens - limits.terminal_reserved_tokens
        )
        else "rework_limit"
        if metadata.rework_count >= limits.rework_rounds
        else None
    )
    if reason is not None:
        targets = [
            ReworkTarget.model_validate(item.model_dump() | {"action": "acknowledge_limit"})
            for item in targets
        ]
        next_value, count = "write", metadata.rework_count
    else:
        action = min((item.action for item in targets), key=_ACTION_PRIORITY.index)
        next_value, count = phase_after_review(action), metadata.rework_count + 1
    return PipelineDecision(
        next_phase=next_value,
        **(
            base
            | {
                "targets": targets,
                "withdraw_claim_ids": sorted(withdrawn),
                "stop_reason": reason,
                "rework_count": count,
            }
        ),
    )


def apply_pipeline_decision(state: PipelineState, decision: PipelineDecision) -> PipelineState:
    """Apply only the exact deterministic decision; report publication is separate."""
    state, decision = PipelineState.model_validate(state), PipelineDecision.model_validate(decision)
    if decision != decide_pipeline(state) or decision.deliver:
        raise ValueError("Only a matching non-delivery policy decision can become a checkpoint")
    claims = dict(state.claims)
    for key in decision.withdraw_claim_ids:
        claims[key] = type(claims[key]).model_validate(
            claims[key].model_dump()
            | {
                "status": "insufficient",
                "status_reason": "Unverified assertion withdrawn by review policy",
            }
        )
    metadata = state.run_metadata.model_dump() | {
        "rework_count": decision.rework_count,
        "rework_targets": decision.targets,
        "stop_reason": decision.stop_reason,
    }
    values = state.model_dump() | {
        "phase": decision.next_phase,
        "claims": claims,
        "run_metadata": metadata,
    }
    if state.phase == "review":
        values |= {"reviewed_draft_version": None, "review_verdict": None}
    return PipelineState.model_validate(values)
