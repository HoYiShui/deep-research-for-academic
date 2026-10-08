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


def materialize_chapter(output, *, section_id, version, values):
    claims, evidence, artifacts = (
        values[name] for name in ("claims", "evidence", "analysis_artifacts")
    )
    statements, bindings = [], []

    def register(text, citation):
        for name, collection in (
            ("claim_ids", claims),
            ("evidence_ids", evidence),
            ("artifact_ids", artifacts),
        ):
            ids = getattr(citation, name)
            if len(set(ids)) != len(ids) or not set(ids) <= collection.keys():
                raise ValueError("Chapter cites an unknown or repeated fact")
        if citation.kind == "factual":
            if not citation.claim_ids or not citation.evidence_ids:
                raise ValueError("Factual prose needs original evidence and a claim")
            for key in citation.claim_ids:
                claim = claims[key]
                if claim.claim_type not in {
                    "factual",
                    "empirical_comparison",
                } or claim.status not in {"supported", "limited", "refuted"}:
                    raise ValueError("Unverified hypothesis cannot become factual prose")
        related = {
            link.evidence_id
            for link in values["claim_evidence_links"]
            if link.claim_id in citation.claim_ids
        }
        for key in citation.artifact_ids:
            if artifacts[key].execution_status != "completed":
                raise ValueError("Incomplete analysis cannot be cited as computed evidence")
            related.update(artifacts[key].input_evidence_ids)
        if not set(citation.evidence_ids) <= related:
            raise ValueError("Chapter citation has no claim/evidence relation")
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
        if len(rows) != len(output.row_citations):
            raise ValueError("Each task row needs its own classification/citation")
        rendered = []
        for row, citation in zip(rows, output.row_citations, strict=True):
            fields = row.model_dump(mode="json")
            texts = [
                text
                for key, value in fields.items()
                if key != "claim_id"
                for text in (value if isinstance(value, list) else [value])
            ]
            if hasattr(row, "claim_id"):
                if row.claim_id not in claims or row.claim_id not in citation.claim_ids:
                    raise ValueError("Protocol row needs its registered target claim")
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
        raise ValueError("Only the core chapter has a task payload")
    draft = DraftSection(
        section_id=section_id,
        title=output.title,
        draft_version=version,
        content="pending projection",
        statements=statements,
        task_payload=payload,
    )
    return DraftSection.model_validate(
        draft.model_dump() | {"content": draft_content(draft)}
    ), bindings


async def draft_chapter(llm, *, plan, values, version, timeout_s=60):
    coverage = values["section_coverage"][plan.section_id]
    scoped = dict(values)
    scoped["claims"] = {
        key: item for key, item in values["claims"].items() if key in coverage.claim_ids
    }
    scoped["evidence"] = {
        key: item for key, item in values["evidence"].items() if key in coverage.evidence_ids
    }
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
        "coverage": coverage.model_dump(mode="json"),
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
            materialize_chapter(self, section_id=plan.section_id, version=version, values=scoped)
            return self

    prompt = WRITE_PROMPT_TEMPLATE.format(
        examples=WRITE_FEW_SHOTS,
        schema=json.dumps(ScopedChapter.model_json_schema(), ensure_ascii=False),
        context=json.dumps(context, ensure_ascii=False),
    )
    if len(prompt.encode()) > 192000:
        raise ValueError("Chapter context exceeds its bound; no facts silently discarded")
    output = await complete(
        llm, prompt, ScopedChapter, operation="write", timeout_s=timeout_s, max_chars=128000
    )
    return materialize_chapter(output, section_id=plan.section_id, version=version, values=scoped)
