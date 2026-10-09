"""Same-version review of actual prose, evidence and previously raised issues."""

import json

from pydantic import StrictBool, create_model, model_validator

from domain.research.agents.legacy_critic import review  # noqa: F401
from domain.research.agents.prompts import (
    REVIEW_FEW_SHOTS,
    REVIEW_PROMPT_TEMPLATE,
)
from domain.research.agents.structured import complete
from domain.research.agents.writer import coverage_summary
from domain.research.facts import CriticFeedback
from domain.research.ids import stable_id
from domain.research.models import Record, ReviewVerdict, Text
from domain.research.reporting import draft_content

IssueDraft = create_model(
    "IssueDraft",
    __base__=Record,
    **{
        name: (field.annotation, field)
        for name, field in CriticFeedback.model_fields.items()
        if name
        in {
            "target_type",
            "target_id",
            "section_id",
            "issue_type",
            "severity",
            "fillable",
            "description",
        }
    },
)


class _Issue(IssueDraft):
    # fillable only routes missing_source rework; code owns it elsewhere.
    fillable: bool = False

    @model_validator(mode="after")
    def fillable_scope(self):
        if self.issue_type != "missing_source" and self.fillable:
            object.__setattr__(self, "fillable", False)
        return self


IssueDraft = _Issue


class Resolution(Record):
    issue_id: Text
    resolved: StrictBool
    resolution: Text | None


class DraftReview(Record):
    issues: list[IssueDraft]
    prior_issues: list[Resolution]
    verdict: ReviewVerdict


def _target_exists(issue, values):
    collections = {
        "source": "sources",
        "evidence": "evidence",
        "claim": "claims",
        "metric": "comparable_metrics",
        "comparison_set": "comparison_sets",
        "artifact": "analysis_artifacts",
        "draft_section": "draft_sections",
    }
    if issue.section_id not in values["draft_sections"]:
        return False
    if issue.target_type == "statement":
        return issue.target_id in {
            item.statement_id for item in values["draft_sections"][issue.section_id].statements
        }
    return issue.target_id in values[collections[issue.target_type]]


def check_draft(values):
    bindings = {
        (binding.section_id, binding.statement_id): binding
        for binding in values["draft_claim_bindings"]
    }
    if len(bindings) != len(values["draft_claim_bindings"]):
        raise ValueError("Draft contains repeated bindings")
    for section in values["draft_sections"].values():
        if not section.statements or section.content != draft_content(section):
            raise ValueError("Review needs the actual registered draft text")
        for statement in section.statements:
            binding = bindings.get((section.section_id, statement.statement_id))
            if statement.kind == "factual" and (
                binding is None or not binding.claim_ids or not binding.cited_evidence_ids
            ):
                raise ValueError("Factual statement has no original evidence binding")
            if binding is not None and (
                not set(binding.claim_ids) <= values["claims"].keys()
                or not set(binding.cited_evidence_ids) <= values["evidence"].keys()
                or not set(binding.artifact_ids) <= values["analysis_artifacts"].keys()
            ):
                raise ValueError("Draft has dangling citation identities")


async def review_draft(llm, *, values, timeout_s=60):
    check_draft(values)
    old = {item.issue_id: item for item in values["critic_feedback"]}

    class ScopedReview(DraftReview):
        @model_validator(mode="after")
        def known_targets(self):
            if any(not _target_exists(issue, values) for issue in self.issues):
                raise ValueError("Review points at an unknown draft/fact")
            ids = [item.issue_id for item in self.prior_issues]
            if len(set(ids)) != len(ids) or set(ids) != old.keys():
                raise ValueError("Review must explicitly recheck every prior issue")
            if any(item.resolved and not item.resolution for item in self.prior_issues):
                raise ValueError("Issue resolution needs current-version reasoning")
            if any(
                issue.fillable and issue.issue_type != "missing_source" for issue in self.issues
            ):
                raise ValueError("Only missing_source has a fillable flag")
            return self

    # The Critic judges the actual draft and the facts it cites. Uncited facts
    # stay in state; per-spec coverage summaries expose what remains unproven.
    bindings = values["draft_claim_bindings"]
    claim_ids = {key for item in bindings for key in item.claim_ids}
    claim_ids |= {
        issue.target_id
        for issue in old.values()
        if issue.target_type == "claim" and issue.target_id in values["claims"]
    }
    evidence_ids = {key for item in bindings for key in item.cited_evidence_ids}
    artifact_ids = {key for item in bindings for key in item.artifact_ids}
    links = [
        item
        for item in values["claim_evidence_links"]
        if item.claim_id in claim_ids and item.evidence_id in evidence_ids
    ]
    source_ids = {values["evidence"][key].source_id for key in evidence_ids}

    def subset(name, keys):
        return {key: values[name][key].model_dump(mode="json") for key in sorted(keys)}

    context = {
        "draft_sections": subset("draft_sections", values["draft_sections"].keys()),
        "claims": subset("claims", claim_ids),
        "evidence": subset("evidence", evidence_ids),
        "sources": subset("sources", source_ids),
        "analysis_artifacts": subset("analysis_artifacts", artifact_ids),
        "comparison_sets": subset("comparison_sets", values["comparison_sets"].keys()),
        "comparable_metrics": subset("comparable_metrics", values["comparable_metrics"].keys()),
        "section_coverage": {
            key: coverage_summary(item, values["claims"])
            for key, item in values["section_coverage"].items()
        },
        "brief": values["research_brief"].model_dump(mode="json"),
        "draft_version": values["draft_version"],
        "bindings": [item.model_dump(mode="json") for item in bindings],
        "claim_evidence_links": [item.model_dump(mode="json") for item in links],
        "previous_issues": [item.model_dump(mode="json") for item in old.values()],
    }
    prompt = REVIEW_PROMPT_TEMPLATE.format(
        examples=REVIEW_FEW_SHOTS,
        schema=json.dumps(ScopedReview.model_json_schema(), ensure_ascii=False),
        context=json.dumps(context, ensure_ascii=False),
    )
    if len(prompt.encode()) > 384000:
        raise ValueError(
            "Review context exceeds its bound; actual draft cannot be silently clipped"
        )
    output = await complete(
        llm, prompt, ScopedReview, operation="review", timeout_s=timeout_s, max_chars=128000
    )
    version = values["draft_version"]
    feedback = [
        CriticFeedback.model_validate(
            old[item.issue_id].model_dump()
            | {
                "resolved": item.resolved,
                "resolution": item.resolution,
                "resolved_in_version": version if item.resolved else None,
            }
        )
        for item in output.prior_issues
    ]
    for issue in output.issues:
        feedback.append(
            CriticFeedback.model_validate(
                issue.model_dump()
                | {
                    "issue_id": stable_id("issue", version, issue.model_dump()),
                    "draft_version": version,
                    "resolved": False,
                    "resolution": None,
                    "resolved_in_version": None,
                }
            )
        )
    incomplete = any(
        coverage.gaps or coverage.unresolved_items
        for coverage in values["section_coverage"].values()
    )
    unsafe = any(not item.resolved and item.severity != "minor" for item in feedback)
    verdict = "needs_more_work" if incomplete or unsafe else output.verdict
    return {
        "critic_feedback": feedback,
        "reviewed_draft_version": version,
        "review_verdict": verdict,
    }
