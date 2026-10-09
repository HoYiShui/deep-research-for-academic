"""Scoped drafting: models supply prose, code owns identity and projection."""

import json
from typing import Annotated, Literal

from pydantic import Field, create_model, model_validator

from domain.research.agents.legacy_writer import revise_report, write_report  # noqa: F401
from domain.research.agents.prompts import (
    WRITE_FEW_SHOTS,
    WRITE_PROMPT_TEMPLATE,
)
from domain.research.agents.structured import complete
from domain.research.diagnostics import diagnostic
from domain.research.facts import (
    CandidateQuestion,
    DraftClaimBinding,
    DraftSection,
    EvaluationPayload,
    IdeaPayload,
    MethodPayload,
    MethodRow,
    ProtocolRow,
    Statement,
)
from domain.research.ids import stable_id
from domain.research.models import Record, Text
from domain.research.reporting import draft_content

CONTEXT_FACT_BYTES = 100_000  # UTF-8 bytes of selected claims + evidence
_STATUS_RANK = {"supported": 0, "limited": 1, "refuted": 2, "open": 3, "insufficient": 4}


def select_chapter_facts(coverage, values, budget=CONTEXT_FACT_BYTES):
    """Pick the chapter's best-supported claims and their evidence within a budget.

    Ranking is deterministic: verified status first, then breadth of original
    support. Omitted claims are counted and returned to the Writer so it can
    disclose them; they remain in the state for later rework or review.
    """
    links = {}
    for link in values["claim_evidence_links"]:
        if link.claim_id in coverage.claim_ids and link.evidence_id in coverage.evidence_ids:
            links.setdefault(link.claim_id, []).append(link.evidence_id)
    ranked = sorted(
        coverage.claim_ids,
        key=lambda key: (
            _STATUS_RANK[values["claims"][key].status],
            -len(set(links.get(key, []))),
            key,
        ),
    )
    claims, evidence, size = [], {}, 0
    for key in ranked:
        new = [item for item in dict.fromkeys(links.get(key, [])) if item not in evidence]
        cost = len(values["claims"][key].model_dump_json().encode()) + sum(
            len(values["evidence"][item].model_dump_json().encode()) for item in new
        )
        if claims and size + cost > budget:
            continue
        claims.append(key)
        evidence.update(dict.fromkeys(new))
        size += cost
    left = [key for key in ranked if key not in set(claims)]
    omitted = {
        "count": len(left),
        "by_status": {
            status: sum(values["claims"][key].status == status for key in left)
            for status in _STATUS_RANK
            if any(values["claims"][key].status == status for key in left)
        },
    }
    if left:
        diagnostic("writer_context_selection", kept=len(claims), omitted=omitted)
    return claims, list(evidence), omitted


def coverage_summary(coverage, claims):
    """Per-ClaimSpec查证现状; the full gap list repeats one row per claim."""
    specs = {}
    for spec_id in coverage.claim_spec_ids:
        gaps = [gap for gap in coverage.gaps if gap.claim_spec_id == spec_id]
        specs[spec_id] = {
            "covered_claim_ids": [
                key
                for key in coverage.covered_claim_ids
                if key in claims and spec_id in claims[key].spec_ids
            ],
            "gap_count": len(gaps),
            "gap_reasons": sorted({gap.reason for gap in gaps}),
            "fillable_gaps": sum(gap.fillable for gap in gaps),
        }
    return {"section_id": coverage.section_id, "claim_specs": specs}


def _row_content(model):
    return create_model(
        model.__name__ + "Content",
        __base__=Record,
        **{
            name: (field.annotation, field)
            for name, field in model.model_fields.items()
            if name != "statement_ids"
        },
    )


CandidateContent = _row_content(CandidateQuestion)
MethodContent = _row_content(MethodRow)
ProtocolContent = _row_content(ProtocolRow)


class Citation(Record):
    kind: Literal["factual", "hypothesis", "recommendation", "limitation"]
    claim_ids: list[Text]
    evidence_ids: list[Text]
    artifact_ids: list[Text]


class Paragraph(Citation):
    text: Text


class IdeaContent(Record):
    task_type: Literal["idea_exploration"]
    candidate_questions: Annotated[list[CandidateContent], Field(min_length=1)]
    recommendation: Text
    minimal_validation: Text


class MethodContentPayload(Record):
    task_type: Literal["method_differentiation"]
    comparison_rows: Annotated[list[MethodContent], Field(min_length=1)]
    differential_claims: Annotated[list[Text], Field(min_length=1)]
    contribution_boundary: Text


class EvaluationContent(Record):
    task_type: Literal["evaluation_design"]
    protocol_rows: Annotated[list[ProtocolContent], Field(min_length=1)]
    failure_modes: Annotated[list[Text], Field(min_length=1)]


TaskContent = Annotated[
    IdeaContent | MethodContentPayload | EvaluationContent, Field(discriminator="task_type")
]


class ChapterDraft(Record):
    title: Text
    paragraphs: Annotated[list[Paragraph], Field(min_length=1, max_length=40)]
    task_payload: TaskContent | None
    row_citations: list[Citation]


def _rows(payload):
    if payload.task_type == "idea_exploration":
        return payload.candidate_questions, [payload.recommendation, payload.minimal_validation]
    if payload.task_type == "method_differentiation":
        return payload.comparison_rows, [
            *payload.differential_claims,
            payload.contribution_boundary,
        ]
    return payload.protocol_rows, payload.failure_modes


def materialize_chapter(output, *, section_id, version, values, report=True):
    claims = values["claims"]
    statements, bindings, repairs = [], [], []

    def register(text, citation):
        # Deterministic repair, not rejection: code removes what it cannot
        # verify and downgrades unsupported prose. The Critic still reviews
        # the resulting statement kinds; only structure errors fail a chapter.
        citation = repair_citation(citation, values, repairs, section_id)
        identity = stable_id("statement", section_id, len(statements), text)
        statements.append(Statement(statement_id=identity, text=text, kind=citation.kind))
        if citation.claim_ids or citation.evidence_ids or citation.artifact_ids:
            bindings.append(
                DraftClaimBinding(
                    draft_version=version,
                    section_id=section_id,
                    statement_id=identity,
                    claim_ids=citation.claim_ids,
                    cited_evidence_ids=citation.evidence_ids,
                    artifact_ids=citation.artifact_ids,
                )
            )
        return identity

    for paragraph in output.paragraphs:
        register(paragraph.text, paragraph)
    payload = None
    if section_id == "section_3":
        if (
            output.task_payload is None
            or output.task_payload.task_type != values["research_brief"].task_type
        ):
            raise ValueError("Core chapter needs the frozen task's payload")
        rows, summaries = _rows(output.task_payload)
        citations = list(output.row_citations[: len(rows)])
        if len(citations) != len(rows):
            repairs.append(
                {
                    "section_id": section_id,
                    "repair": "row_citations_resized",
                    "rows": len(rows),
                    "citations": len(output.row_citations),
                }
            )
        citations += [_UNCITED] * (len(rows) - len(citations))
        rendered = []
        for row, citation in zip(rows, citations, strict=True):
            fields = row.model_dump(mode="json")
            texts = [
                text
                for key, value in fields.items()
                if key != "claim_id"
                for text in (value if isinstance(value, list) else [value])
            ]
            if hasattr(row, "claim_id"):
                if row.claim_id not in claims:
                    raise ValueError(
                        f"Protocol row claim_id {row.claim_id!r} is not one of the supplied claims"
                    )
                if row.claim_id not in citation.claim_ids:
                    citation = citation.model_copy(
                        update={"claim_ids": [*citation.claim_ids, row.claim_id]}
                    )
                texts.insert(0, claims[row.claim_id].text)
            identity = register("；".join(texts), citation)
            rendered.append(fields | {"statement_ids": [identity]})
        register(
            "；".join(summaries),
            Citation(kind="recommendation", claim_ids=[], evidence_ids=[], artifact_ids=[]),
        )
        data = output.task_payload.model_dump(mode="json")
        key, cls = {
            "idea_exploration": ("candidate_questions", IdeaPayload),
            "method_differentiation": ("comparison_rows", MethodPayload),
            "evaluation_design": ("protocol_rows", EvaluationPayload),
        }[data["task_type"]]
        payload = cls.model_validate(data | {key: rendered})
    elif output.task_payload is not None or output.row_citations:
        repairs.append({"section_id": section_id, "repair": "dropped_task_payload"})
    draft = DraftSection(
        section_id=section_id,
        title=output.title,
        draft_version=version,
        content="pending projection",
        statements=statements,
        task_payload=payload,
    )
    for item in repairs if report else ():
        diagnostic("writer_repair", **item)
    return DraftSection.model_validate(
        draft.model_dump() | {"content": draft_content(draft)}
    ), bindings


_UNCITED = Citation(kind="hypothesis", claim_ids=[], evidence_ids=[], artifact_ids=[])
_VERIFIED = {"supported", "limited", "refuted"}


def repair_citation(citation, values, repairs, section_id):
    """Return a citation that the projection can bind, recording every change."""
    claims, evidence, artifacts = (
        values[name] for name in ("claims", "evidence", "analysis_artifacts")
    )

    def note(repair, **detail):
        repairs.append({"section_id": section_id, "repair": repair, **detail})

    def known(ids, collection, name):
        kept = [key for key in dict.fromkeys(ids) if key in collection]
        if len(kept) != len(ids):
            note("dropped_unknown_ids", field=name, dropped=sorted(set(ids) - set(kept)))
        return kept

    claim_ids = known(citation.claim_ids, claims, "claim_ids")
    evidence_ids = known(citation.evidence_ids, evidence, "evidence_ids")
    artifact_ids = []
    for key in known(citation.artifact_ids, artifacts, "artifact_ids"):
        if artifacts[key].execution_status == "completed":
            artifact_ids.append(key)
        else:
            note("dropped_incomplete_artifact", artifact_id=key)
    related = {
        link.evidence_id for link in values["claim_evidence_links"] if link.claim_id in claim_ids
    }
    for key in artifact_ids:
        related.update(artifacts[key].input_evidence_ids)
    unrelated = [key for key in evidence_ids if key not in related]
    if unrelated:
        note("dropped_unrelated_evidence", evidence_ids=unrelated, claim_ids=claim_ids)
        evidence_ids = [key for key in evidence_ids if key in related]
    kind = citation.kind
    if kind == "factual":
        verified = [
            key
            for key in claim_ids
            if claims[key].claim_type in {"factual", "empirical_comparison"}
            and claims[key].status in _VERIFIED
        ]
        if (
            not verified
            or not evidence_ids
            or len(verified) != len(claim_ids)
            or not all(_tier_backed(key, evidence_ids, values) for key in claim_ids)
        ):
            note("factual_downgraded", claim_ids=claim_ids, evidence_ids=evidence_ids)
            kind = "hypothesis"
    return Citation(
        kind=kind, claim_ids=claim_ids, evidence_ids=evidence_ids, artifact_ids=artifact_ids
    )


def _tier_backed(claim_id, evidence_ids, values):
    """Mirror of the delivery gate: factual prose needs planned-tier evidence."""
    claim = values["claims"][claim_id]
    relations = (
        {"refutes"}
        if claim.status == "refuted"
        else {"supports"}
        if claim.status == "supported"
        else {"supports", "limits", "refutes"}
    )
    linked = [
        values["evidence"][link.evidence_id]
        for link in values["claim_evidence_links"]
        if link.claim_id == claim_id
        and link.evidence_id in evidence_ids
        and link.relation in relations
    ]
    if not linked:
        return False
    specs = {spec.spec_id: spec for plan in values["section_plans"] for spec in plan.claim_specs}
    return all(
        not specs[spec_id].required_source_tiers
        or any(
            values["sources"][item.source_id].source_tier in specs[spec_id].required_source_tiers
            for item in linked
        )
        for spec_id in claim.spec_ids
        if spec_id in specs
    )


def downgrade_unbacked_facts(values):
    """Re-apply the factual rule to committed drafts (e.g. after a rule change)."""
    bindings = {
        (item.section_id, item.statement_id): item for item in values["draft_claim_bindings"]
    }
    sections = {}
    for key, section in values["draft_sections"].items():
        statements = []
        for statement in section.statements:
            binding = bindings.get((key, statement.statement_id))
            if statement.kind == "factual" and (
                binding is None
                or not binding.cited_evidence_ids
                or not all(
                    _tier_backed(claim_id, binding.cited_evidence_ids, values)
                    for claim_id in binding.claim_ids
                )
                or not binding.claim_ids
            ):
                statement = statement.model_copy(update={"kind": "hypothesis"})
            statements.append(statement)
        draft = section.model_copy(update={"statements": statements})
        sections[key] = DraftSection.model_validate(
            draft.model_dump() | {"content": draft_content(draft)}
        )
    return sections


async def draft_chapter(llm, *, plan, values, version, timeout_s=60):
    coverage = values["section_coverage"][plan.section_id]
    scoped = dict(values)
    # Revision context (previous draft, feedback) shares the prompt bound.
    revision = len(
        json.dumps(
            [
                values["draft_sections"][plan.section_id].model_dump(mode="json")
                if plan.section_id in values["draft_sections"]
                else None,
                [item.model_dump(mode="json") for item in values["critic_feedback"]],
            ],
            ensure_ascii=False,
        ).encode()
    )
    claim_ids, evidence_ids, omitted = select_chapter_facts(
        coverage, values, budget=max(20_000, CONTEXT_FACT_BYTES - revision)
    )
    scoped["claims"] = {key: values["claims"][key] for key in claim_ids}
    scoped["evidence"] = {key: values["evidence"][key] for key in evidence_ids}
    scoped["analysis_artifacts"] = {
        key: item
        for key, item in values["analysis_artifacts"].items()
        if item.section_id == plan.section_id
    }
    groups = {
        key: item
        for key, item in values["comparison_sets"].items()
        if item.section_id == plan.section_id
    }
    metric_ids = {key for group in groups.values() for key in group.metric_ids}
    scoped["evidence"].update(
        {
            key: values["evidence"][key]
            for artifact in scoped["analysis_artifacts"].values()
            for key in artifact.input_evidence_ids
        }
    )
    context = {
        "brief": values["research_brief"].model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "coverage": coverage_summary(coverage, scoped["claims"]),
        "claims": {key: item.model_dump(mode="json") for key, item in scoped["claims"].items()},
        "evidence": {key: item.model_dump(mode="json") for key, item in scoped["evidence"].items()},
        "claim_evidence_links": [
            item.model_dump(mode="json")
            for item in values["claim_evidence_links"]
            if item.claim_id in scoped["claims"] and item.evidence_id in scoped["evidence"]
        ],
        "comparison_sets": {key: item.model_dump(mode="json") for key, item in groups.items()},
        "comparable_metrics": {
            key: item.model_dump(mode="json")
            for key, item in values["comparable_metrics"].items()
            if key in metric_ids
        },
        "sources": {
            key: item.model_dump(mode="json")
            for key, item in values["sources"].items()
            if key in {e.source_id for e in scoped["evidence"].values()}
        },
        "analysis": {
            key: item.model_dump(mode="json") for key, item in scoped["analysis_artifacts"].items()
        },
        "omitted_claims": omitted,
        "previous_draft": values["draft_sections"][plan.section_id].model_dump(mode="json")
        if plan.section_id in values["draft_sections"]
        else None,
        "feedback": [
            item.model_dump(mode="json")
            for item in values["critic_feedback"]
            if item.section_id == plan.section_id and not item.resolved
        ],
    }

    # The model receives the actual chapter contract, not the broad union and
    # a prose request to remember which combinations are legal.
    if plan.section_id == "section_3":
        payload_type = {
            "idea_exploration": IdeaContent,
            "method_differentiation": MethodContentPayload,
            "evaluation_design": EvaluationContent,
        }[values["research_brief"].task_type]
        chapter_model = create_model(
            "CoreChapter",
            __base__=ChapterDraft,
            task_payload=(payload_type, ...),
            row_citations=(Annotated[list[Citation], Field(min_length=1)], ...),
        )
    else:
        chapter_model = create_model(
            "PlainChapter",
            __base__=ChapterDraft,
            task_payload=(type(None), ...),
            row_citations=(Annotated[list[Citation], Field(max_length=0)], ...),
        )

    class ScopedChapter(chapter_model):
        @model_validator(mode="after")
        def valid_references(self):
            materialize_chapter(
                self, section_id=plan.section_id, version=version, values=scoped, report=False
            )
            return self

    prompt = WRITE_PROMPT_TEMPLATE.format(
        examples=WRITE_FEW_SHOTS,
        schema=json.dumps(ScopedChapter.model_json_schema(), ensure_ascii=False),
        context=json.dumps(context, ensure_ascii=False),
    )
    if len(prompt.encode()) > 192000:
        raise ValueError("Chapter context exceeds its bound; no facts silently discarded")
    output = await complete(
        llm,
        prompt,
        ScopedChapter,
        operation="write",
        timeout_s=timeout_s,
        max_chars=128000,
        drop_extra=True,
    )
    return materialize_chapter(output, section_id=plan.section_id, version=version, values=scoped)
