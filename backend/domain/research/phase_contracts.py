"""Pure worker authority and semantic input boundaries for mono phases."""

from typing import Annotated, Literal

from pydantic import TypeAdapter, model_validator

from domain.research.facts import ReworkTarget
from domain.research.ids import canonical_hash
from domain.research.models import Failure, Hash, Record, Text
from domain.research.state import Degradation, PipelineState

WorkerPhase = Literal["plan", "research", "analyze", "write", "review"]
SECTION_IDS = {f"section_{index}" for index in range(1, 6)}
_COMMON = {"research_brief", "source_selection", "section_plans"}
_FACTS = {
    "sources",
    "evidence",
    "claims",
    "claim_evidence_links",
    "quantitative_observations",
    "section_coverage",
}
_ANALYSIS = {"comparable_metrics", "comparison_sets", "analysis_artifacts"}
READS = {
    "plan": {"research_brief", "source_selection"},
    "research": _COMMON | _FACTS,
    "analyze": _COMMON | _FACTS | _ANALYSIS,
    "write": _COMMON
    | _FACTS
    | _ANALYSIS
    | {"draft_sections", "draft_claim_bindings", "draft_version", "critic_feedback"},
    "review": _COMMON
    | _FACTS
    | _ANALYSIS
    | {"draft_sections", "draft_claim_bindings", "draft_version", "critic_feedback"},
}
WRITES = {
    "plan": {"section_plans"},
    "research": _FACTS,
    "analyze": _ANALYSIS | {"section_coverage"},
    "write": {"draft_sections", "draft_claim_bindings", "draft_version"},
    "review": {"critic_feedback", "reviewed_draft_version", "review_verdict"},
}


def _adapter(name):
    field = PipelineState.model_fields[name]
    annotation = (
        Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation
    )
    return TypeAdapter(annotation)


def validate_plans(plans):
    if len(plans) != 5 or {plan.section_id for plan in plans} != SECTION_IDS:
        raise ValueError("Plan must cover exactly five sections")
    if not any(plan.claim_specs for plan in plans) or not any(plan.sub_questions for plan in plans):
        raise ValueError("The whole plan requires a claim spec and retrieval question")
    specs = {}
    for plan in plans:
        for spec in plan.claim_specs:
            if spec.spec_id in specs and specs[spec.spec_id] != spec:
                raise ValueError("Shared claim spec IDs must describe the same requirement")
            specs[spec.spec_id] = spec
    requirements = [item.requirement_id for plan in plans for item in plan.analysis_requirements]
    if len(requirements) != len(set(requirements)):
        raise ValueError("Plan analysis requirement IDs must be globally unique")
    for plan in plans:
        ids = {spec.spec_id for spec in plan.claim_specs}
        if any(not set(item.claim_spec_ids) <= ids for item in plan.analysis_requirements):
            raise ValueError("Analysis requirements must reference this section's specs")
    return plans


def _coverage(state):
    plans = {
        plan.section_id: {spec.spec_id for spec in plan.claim_specs} for plan in state.section_plans
    }
    for key, coverage in state.section_coverage.items():
        specs = plans.get(key, set())
        if (
            not set(coverage.claim_spec_ids) <= specs
            or not set(coverage.claim_ids) <= set(state.claims)
            or not set(coverage.evidence_ids) <= set(state.evidence)
            or not set(coverage.covered_claim_ids) <= set(coverage.claim_ids)
            or any(
                gap.section_id != key
                or gap.claim_spec_id is not None
                and gap.claim_spec_id not in specs
                or gap.claim_id is not None
                and gap.claim_id not in state.claims
                for gap in coverage.gaps
            )
        ):
            raise ValueError("Coverage references missing or wrong-section facts")


class PhaseInput(Record):
    phase: WorkerPhase
    values: dict

    @model_validator(mode="after")
    def validate_slice(self):
        if set(self.values) != READS[self.phase] | {"rework_targets"}:
            raise ValueError("Phase input must contain exactly its permitted read slice")
        values = {
            name: _adapter(name).validate_python(value)
            for name, value in self.values.items()
            if name != "rework_targets"
        }
        values["rework_targets"] = TypeAdapter(list[ReworkTarget]).validate_python(
            self.values["rework_targets"]
        )
        if self.phase != "plan":
            validate_plans(values["section_plans"])
        if self.phase == "write":
            if set(values["section_coverage"]) != SECTION_IDS:
                raise ValueError("Writing requires coverage for all sections")
            if not values["evidence"] and any(
                not coverage.gaps and not coverage.unresolved_items
                for coverage in values["section_coverage"].values()
            ):
                raise ValueError(
                    "Evidence-free writing requires explicit insufficiency per section"
                )
        if self.phase == "review":
            if set(values["draft_sections"]) != SECTION_IDS or values["draft_version"] < 1:
                raise ValueError("Review requires the complete current draft")
            if any(
                section.draft_version != values["draft_version"]
                for section in values["draft_sections"].values()
            ) or any(
                binding.draft_version != values["draft_version"]
                for binding in values["draft_claim_bindings"]
            ):
                raise ValueError("Review input mixes draft versions")
        object.__setattr__(self, "values", values)
        return self

    @classmethod
    def from_state(cls, state):
        state = PipelineState.model_validate(state)
        if state.phase not in READS:
            raise ValueError("A terminal state has no worker phase")
        _coverage(state)
        serialized = state.model_dump(mode="json")
        values = {name: serialized[name] for name in READS[state.phase]}
        values["rework_targets"] = serialized["run_metadata"]["rework_targets"]
        return cls(phase=state.phase, values=values)

    @property
    def semantic_hash(self):
        return canonical_hash(self)


class PhaseResult(Record):
    phase: WorkerPhase
    unit_id: Text
    input_hash: Hash
    changes: dict
    degradations: list[Degradation]
    failures: list[Failure]

    @model_validator(mode="after")
    def validate_changes(self):
        if not set(self.changes) <= WRITES[self.phase]:
            raise ValueError("Phase result attempts an unauthorized State write")
        object.__setattr__(
            self,
            "changes",
            {name: _adapter(name).validate_python(value) for name, value in self.changes.items()},
        )
        if any(failure.phase != self.phase for failure in self.failures):
            raise ValueError("Failure must describe the requested phase")
        return self


def _append_map(existing, additions, *, mutable=()):
    result = dict(existing)
    for key, record in additions.items():
        old = result.get(key)
        if old is not None and old.model_dump(exclude=set(mutable)) != record.model_dump(
            exclude=set(mutable)
        ):
            raise ValueError("Stable fact identity cannot overwrite committed content")
        if old is not None and "provenance" in mutable:
            provenance = {
                canonical_hash(item): item for item in [*old.provenance, *record.provenance]
            }
            record = type(record).model_validate(
                record.model_dump() | {"provenance": list(provenance.values())}
            )
        if old is not None and "spec_ids" in mutable:
            record = type(record).model_validate(
                record.model_dump() | {"spec_ids": sorted(set(old.spec_ids) | set(record.spec_ids))}
            )
        result[key] = record
    return result


def _append_list(existing, additions, identity):
    result = {identity(item): item for item in existing}
    for item in additions:
        key = identity(item)
        if key in result and result[key] != item:
            raise ValueError("Stable relation cannot overwrite committed rationale")
        result[key] = item
    return list(result.values())


def merge_phase_result(state, result, *, target_sections=None, target_requirements=None):
    """Merge only authorized facts; no phase advance, budget spend or report creation.

    Target scopes are supplied by trusted orchestration code, never by model output.
    All changes are validated on a fresh full State before caller can persist it.
    """
    state = PipelineState.model_validate(state)
    result = PhaseResult.model_validate(result)
    if (
        result.phase != state.phase
        or result.input_hash != PhaseInput.from_state(state).semantic_hash
    ):
        raise ValueError("Phase result does not match the current semantic input")
    sections = set(target_sections) if target_sections is not None else SECTION_IDS
    if not sections or not sections <= SECTION_IDS:
        raise ValueError("Unknown merge target section")
    changes = result.changes
    values = state.model_dump()
    if state.phase == "plan":
        if set(changes) != {"section_plans"} or sections != SECTION_IDS:
            raise ValueError("Planning must replace the complete five-section plan")
        values["section_plans"] = validate_plans(changes["section_plans"])
    elif state.phase == "research":
        specs = {
            spec.spec_id
            for plan in state.section_plans
            if plan.section_id in sections
            for spec in plan.claim_specs
        }
        if any(
            not set(claim.spec_ids) & specs
            or not set(claim.spec_ids)
            <= (
                specs | set(state.claims[claim.claim_id].spec_ids)
                if claim.claim_id in state.claims
                else specs
            )
            for claim in changes.get("claims", {}).values()
        ):
            raise ValueError("Research claim escapes its target sections")
        combined_claims = state.claims | changes.get("claims", {})
        if any(
            link.claim_id not in combined_claims
            or not set(combined_claims[link.claim_id].spec_ids) & specs
            for link in changes.get("claim_evidence_links", [])
        ):
            raise ValueError("Research relation escapes its target claims")
        for name in ("sources", "evidence", "claims", "quantitative_observations"):
            mutable = (
                {"status", "status_reason", "spec_ids"}
                if name == "claims"
                else {"provenance"}
                if name == "sources"
                else set()
            )
            values[name] = _append_map(getattr(state, name), changes.get(name, {}), mutable=mutable)
        values["claim_evidence_links"] = _append_list(
            state.claim_evidence_links,
            changes.get("claim_evidence_links", []),
            lambda item: (item.claim_id, item.evidence_id, item.relation),
        )
    elif state.phase == "analyze":
        _merge_analysis(state, changes, values, sections, target_requirements)
    elif state.phase == "write":
        _merge_write(state, changes, values, sections)
    else:
        _merge_review(state, changes, values)
    coverage = changes.get("section_coverage", {})
    if not set(coverage) <= sections:
        raise ValueError("Coverage update escapes target sections")
    if coverage:
        values["section_coverage"] = state.section_coverage | coverage
    metadata = state.run_metadata.model_dump()
    if state.phase == "analyze" and set(changes) & _ANALYSIS:
        # Recomputed derived facts invalidate only their target chapters. Keeping
        # stale bindings would dangle after replacement or cite obsolete analysis.
        affected = set(state.draft_sections) & sections
        if affected:
            values["draft_sections"] = {
                key: item for key, item in state.draft_sections.items() if key not in affected
            }
            values["draft_claim_bindings"] = [
                item for item in state.draft_claim_bindings if item.section_id not in affected
            ]
            values["reviewed_draft_version"] = None
            values["review_verdict"] = None
            invalidation = ReworkTarget(
                issue_ids=[],
                section_ids=sorted(affected),
                claim_ids=[],
                action="revise",
                reason="Derived analysis changed; rewrite affected draft chapters",
            )
            metadata["rework_targets"] = _append_list(
                state.run_metadata.rework_targets, [invalidation], canonical_hash
            )
    metadata["degraded_sources"] = _append_list(
        state.run_metadata.degraded_sources, result.degradations, canonical_hash
    )
    values["run_metadata"] = metadata
    values["errors"] = _append_list(state.errors, result.failures, canonical_hash)
    merged = PipelineState.model_validate(values)
    _coverage(merged)
    return merged


def _merge_analysis(state, changes, values, sections, target_requirements):
    if not (set(changes) & _ANALYSIS):
        return
    if not _ANALYSIS <= set(changes):
        raise ValueError("An analysis unit must return all three derived collections")
    allowed = {
        item.requirement_id
        for plan in state.section_plans
        if plan.section_id in sections
        for item in plan.analysis_requirements
    }
    requirements = set(target_requirements) if target_requirements is not None else allowed
    if not requirements or not requirements <= allowed:
        raise ValueError("Unknown analysis requirement scope")
    groups = changes["comparison_sets"]
    if any(
        group.section_id not in sections or group.requirement_id not in requirements
        for group in groups.values()
    ):
        raise ValueError("Analysis escapes its target requirements")
    old_targets = {
        key
        for key, group in state.comparison_sets.items()
        if group.section_id in sections and group.requirement_id in requirements
    }
    remaining = {
        key: group for key, group in state.comparison_sets.items() if key not in old_targets
    }
    values["comparison_sets"] = _append_map(remaining, groups)
    shared = {key for group in remaining.values() for key in group.metric_ids}
    removed = {
        key for group_id in old_targets for key in state.comparison_sets[group_id].metric_ids
    } - shared
    metrics = {
        key: metric for key, metric in state.comparable_metrics.items() if key not in removed
    }
    if not set(changes["comparable_metrics"]) <= {
        key for group in groups.values() for key in group.metric_ids
    }:
        raise ValueError("Derived metrics must belong to target comparison sets")
    values["comparable_metrics"] = _append_map(metrics, changes["comparable_metrics"])
    artifacts = {
        key: item
        for key, item in state.analysis_artifacts.items()
        if item.comparison_set_id not in old_targets
    }
    if any(
        item.section_id not in sections or item.comparison_set_id not in groups
        for item in changes["analysis_artifacts"].values()
    ):
        raise ValueError("Derived artifact must belong to the target comparison set")
    values["analysis_artifacts"] = _append_map(artifacts, changes["analysis_artifacts"])


def _merge_write(state, changes, values, sections):
    if set(changes) != WRITES["write"] or changes["draft_version"] != state.draft_version + 1:
        raise ValueError("Writing requires all draft fields and one new global version")
    version = changes["draft_version"]
    if not set(changes["draft_sections"]) <= sections or any(
        item.section_id not in sections for item in changes["draft_claim_bindings"]
    ):
        raise ValueError("Draft update escapes target sections")
    if set(changes["draft_sections"]) != sections:
        raise ValueError("Writing must replace all target chapters")
    drafts = dict(state.draft_sections)
    drafts.update(changes["draft_sections"])
    if (
        set(drafts) != SECTION_IDS
        or any(item.draft_version != version for item in changes["draft_sections"].values())
        or any(item.draft_version != version for item in changes["draft_claim_bindings"])
    ):
        raise ValueError("Writing must produce a complete same-version draft")
    values["draft_sections"] = {
        key: type(item).model_validate(item.model_dump() | {"draft_version": version})
        for key, item in drafts.items()
    }
    retained = [
        type(item).model_validate(item.model_dump() | {"draft_version": version})
        for item in state.draft_claim_bindings
        if item.section_id not in sections
    ]
    values["draft_claim_bindings"] = retained + changes["draft_claim_bindings"]
    binding_keys = [(item.section_id, item.statement_id) for item in values["draft_claim_bindings"]]
    if len(binding_keys) != len(set(binding_keys)):
        raise ValueError("Draft statement bindings must be unique")
    if any(
        (section.section_id, statement.statement_id) not in set(binding_keys)
        for section in values["draft_sections"].values()
        for statement in section.statements
        if statement.kind == "factual"
    ):
        raise ValueError("Every factual statement requires an explicit binding")
    values["draft_version"] = version
    values["reviewed_draft_version"] = None
    values["review_verdict"] = None


def _merge_review(state, changes, values):
    if (
        set(changes) != WRITES["review"]
        or changes["reviewed_draft_version"] != state.draft_version
        or changes["review_verdict"] is None
    ):
        raise ValueError("Review must judge the complete current draft")
    feedback = {item.issue_id: item for item in state.critic_feedback}
    for item in changes["critic_feedback"]:
        old = feedback.get(item.issue_id)
        if old is not None and old.model_dump(
            exclude={"resolved", "resolution", "resolved_in_version"}
        ) != item.model_dump(exclude={"resolved", "resolution", "resolved_in_version"}):
            raise ValueError("Review cannot replace an existing issue's identity")
        if old is None and item.draft_version != state.draft_version:
            raise ValueError("New review issues must refer to the current draft")
        if old is None:
            collections = {
                "source": state.sources,
                "evidence": state.evidence,
                "claim": state.claims,
                "metric": state.comparable_metrics,
                "comparison_set": state.comparison_sets,
                "artifact": state.analysis_artifacts,
                "draft_section": state.draft_sections,
            }
            targets = collections.get(item.target_type)
            if item.target_type == "statement":
                targets = {
                    statement.statement_id
                    for statement in state.draft_sections[item.section_id].statements
                }
            if targets is None or item.target_id not in targets:
                raise ValueError("Review issue targets an unknown object")
        if item.resolved and (
            not item.resolution or item.resolved_in_version != state.draft_version
        ):
            raise ValueError("Resolved issues require current-version verification")
        feedback[item.issue_id] = item
    values["critic_feedback"] = list(feedback.values())
    values["reviewed_draft_version"] = changes["reviewed_draft_version"]
    values["review_verdict"] = changes["review_verdict"]
