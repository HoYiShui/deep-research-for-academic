"""Deterministic per-spec coverage; model-proposed statuses are not authority."""

from domain.research.facts import (
    Claim,
    ClaimEvidenceLink,
    Evidence,
    Gap,
    SectionCoverage,
    SectionPlan,
    SourceRecord,
)
from domain.research.ids import stable_id


def planned_hypotheses(plan, brief, claims):
    """Registered evaluation targets from the plan, not extracted facts."""
    if plan.section_id != "section_3" or brief.task_type != "evaluation_design":
        return {}
    return {
        stable_id("planned_hypothesis", spec.spec_id, spec.text): Claim(
            claim_id=stable_id("planned_hypothesis", spec.spec_id, spec.text),
            spec_ids=[spec.spec_id],
            text=spec.text,
            claim_type="hypothesis",
            conditions={},
            status="insufficient",
            status_reason="研究计划中的待验证主张；尚无原始证据",
        )
        for spec in plan.claim_specs
        if not any(spec.spec_id in claim.spec_ids for claim in claims.values())
    }


def section_coverage(
    plan: SectionPlan,
    claims: dict[str, Claim],
    evidence: dict[str, Evidence],
    sources: dict[str, SourceRecord],
    links: list[ClaimEvidenceLink],
    *,
    all_specs=None,
) -> tuple[dict[str, Claim], SectionCoverage]:
    """Return relevant status updates and coverage from already-verified facts.

    Conditions are required named attributes, not something this function can
    infer from prose. Source tiers are alternatives allowed by each ClaimSpec.
    Original range verification must precede inserting Evidence into the input.
    """
    for key, fact in claims.items():
        if key != fact.claim_id:
            raise ValueError("Claim map identity differs")
    for key, fact in evidence.items():
        source = sources.get(fact.source_id)
        if (
            key != fact.evidence_id
            or source is None
            or source.source_id != fact.source_id
            or source.content_hash != fact.content_hash
        ):
            raise ValueError("Evidence/source reference differs")
    for link in links:
        if link.claim_id not in claims or link.evidence_id not in evidence:
            raise ValueError("Claim link contains an unknown reference")
    specs = {spec.spec_id: spec for spec in plan.claim_specs}
    global_specs = specs if all_specs is None else all_specs
    updates, used, covered, gaps = {}, set(), set(), []
    covered_specs = set()
    for claim_id, claim in claims.items():
        relevant = set(claim.spec_ids) & specs.keys()
        if not relevant:
            continue
        relations = [link for link in links if link.claim_id == claim_id]
        used.update(link.evidence_id for link in relations)
        supports = [link for link in relations if link.relation == "supports"]
        refutes = any(link.relation == "refutes" for link in relations)
        limits = any(link.relation == "limits" for link in relations)
        qualified = set()
        required = set(claim.spec_ids) if all_specs is not None else relevant
        for spec_id in required:
            if spec_id not in global_specs:
                raise ValueError("Claim references an unknown ClaimSpec")
            spec = global_specs[spec_id]
            conditions_present = all(
                field in claim.conditions and claim.conditions[field] not in (None, "", [])
                for field in spec.required_conditions
            )
            tier_allowed = any(
                not spec.required_source_tiers
                or sources[evidence[link.evidence_id].source_id].source_tier
                in spec.required_source_tiers
                for link in supports
            )
            if conditions_present and tier_allowed:
                qualified.add(spec_id)
        if supports and (refutes or limits):
            status, reason = (
                "limited",
                "Supporting evidence also has refuting or limiting relations",
            )
        elif refutes:
            status, reason = "refuted", "Evidence refutes this claim; support is absent"
        elif qualified == required:
            status, reason = (
                "supported",
                "Support meets all relevant source-tier and condition requirements",
            )
        else:
            status, reason = (
                "insufficient",
                "Missing support, required source tier, or explicit conditions",
            )
        updates[claim_id] = claim.model_copy(update={"status": status, "status_reason": reason})
        if status == "supported":
            covered.add(claim_id)
            covered_specs.update(qualified & relevant)
        else:
            for spec_id in sorted(relevant):
                gaps.append(
                    Gap(
                        gap_id=stable_id("gap", plan.section_id, spec_id, claim_id, status),
                        section_id=plan.section_id,
                        claim_spec_id=spec_id,
                        claim_id=claim_id,
                        reason=reason,
                        fillable=status != "refuted",
                        verification_action="Check original support, conflicting relations, and required conditions",
                    )
                )
    for spec_id in sorted(specs):
        if spec_id in covered_specs or any(gap.claim_spec_id == spec_id for gap in gaps):
            continue
        relevant_claims = sorted(key for key, claim in updates.items() if spec_id in claim.spec_ids)
        reason = (
            "No extracted claim for this ClaimSpec"
            if not relevant_claims
            else "ClaimSpec lacks unqualified support"
        )
        gaps.append(
            Gap(
                gap_id=stable_id("gap", plan.section_id, spec_id, reason),
                section_id=plan.section_id,
                claim_spec_id=spec_id,
                claim_id=relevant_claims[0] if len(relevant_claims) == 1 else None,
                reason=reason,
                fillable=True,
                verification_action="Retrieve original evidence addressing this spec and its required conditions",
            )
        )
    return updates, SectionCoverage(
        section_id=plan.section_id,
        claim_spec_ids=sorted(specs),
        claim_ids=sorted(updates),
        evidence_ids=sorted(used),
        covered_claim_ids=sorted(covered),
        gaps=gaps,
        unresolved_items=[gap.reason for gap in gaps],
    )
