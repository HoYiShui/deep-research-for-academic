"""Scoped original-text extraction; models propose facts, code owns their IDs."""

import json
import re
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from domain.research.agents.originals import evidence_from_original, validate_original
from domain.research.agents.prompts import (
    EXTRACTION_FEW_SHOTS,
    EXTRACTION_PROMPT_TEMPLATE,
)
from domain.research.agents.structured import complete
from domain.research.agents.table_values import cell_values
from domain.research.agents.table_values import normalized as _normalized
from domain.research.diagnostics import diagnostic
from domain.research.facts import (
    Attributes,
    Claim,
    ClaimEvidenceLink,
    ClaimType,
    EvidenceType,
    ObservationKind,
    QuantitativeObservation,
)
from domain.research.ids import stable_id
from domain.research.models import Record, Text


class QuoteDraft(Record):
    key: Text
    block_index: Annotated[StrictInt, Field(ge=0)]
    quote: Text
    evidence_type: EvidenceType


class RelationDraft(Record):
    evidence_key: Text
    relation: Literal["supports", "refutes", "limits"]
    rationale: Text


class ClaimDraft(Record):
    subject: Text
    predicate: Text
    object: Text
    spec_ids: Annotated[list[Text], Field(min_length=1)]
    claim_type: ClaimType
    conditions: Attributes
    relations: Annotated[list[RelationDraft], Field(max_length=32)]


class ObservationDraft(Record):
    evidence_key: Text
    kind: ObservationKind
    row_key: Attributes
    column_key: Attributes
    raw_value: Text
    # Code derives the number from raw_value; a model-typed value is only a
    # hint, so a malformed one cannot void the source's whole extraction.
    value: Text | None = None
    uncertainty: Text | None = None
    statistic: Text
    context: Attributes
    unit: Text | None


class ExtractionOutput(Record):
    evidence: Annotated[list[QuoteDraft], Field(max_length=32)]
    claims: Annotated[list[ClaimDraft], Field(max_length=32)]
    observations: Annotated[list[ObservationDraft], Field(max_length=64)]


def materialize(output, *, source, fetched, parsed, spec_ids, block_ids, report=True, strict=False):
    """Keep every independently valid item; drop and record the rest.

    Each quote, claim, relation and observation is checked against the
    original on its own. One bad number must not discard a source's other
    verified quotes, so item failures become repairs, not chapter failures.
    """
    evidence, claims, links, observations, keys = {}, {}, {}, {}, {}
    repairs = []

    def dropped(kind, index, reason):
        if strict:
            raise ValueError(f"{kind} {index}: {reason}")
        repairs.append({"repair": f"dropped_{kind}", "index": index, "reason": str(reason)[:300]})

    for index, draft in enumerate(output.evidence):
        try:
            _quote(draft, keys, evidence, source, fetched, parsed, block_ids)
        except ValueError as exc:
            dropped("evidence", index, exc)
    for index, draft in enumerate(output.claims):
        try:
            _claim(draft, keys, claims, links, spec_ids, repairs)
        except ValueError as exc:
            dropped("claim", index, exc)
    for index, draft in enumerate(output.observations):
        try:
            _observation(draft, keys, observations)
        except ValueError as exc:
            dropped("observation", index, exc)
    for item in repairs if report else ():
        diagnostic("extraction_repair", source_id=source.source_id, **item)
    return {
        "sources": {source.source_id: source},
        "evidence": evidence,
        "claims": claims,
        "claim_evidence_links": list(links.values()),
        "quantitative_observations": observations,
    }


def _quote(draft, keys, evidence, source, fetched, parsed, block_ids):
    if draft.key in keys or draft.block_index not in block_ids:
        raise ValueError("Evidence key duplicates or escapes supplied original blocks")
    fact = evidence_from_original(
        source,
        fetched,
        parsed,
        block_index=draft.block_index,
        quote=draft.quote,
        evidence_type=draft.evidence_type,
    )
    keys[draft.key] = (fact, parsed.blocks[draft.block_index])
    evidence[fact.evidence_id] = fact


def _claim(draft, keys, claims, links, spec_ids, repairs):
    scoped = sorted(set(draft.spec_ids) & spec_ids)
    if not scoped:
        raise ValueError("Claim names none of the supplied chapter ClaimSpecs")
    if len(scoped) != len(set(draft.spec_ids)):
        repairs.append({"repair": "dropped_unknown_spec_ids", "spec_ids": draft.spec_ids})
    draft = draft.model_copy(update={"spec_ids": scoped})
    natural = [
        _normalized(draft.subject),
        _normalized(draft.predicate),
        _normalized(draft.object),
    ]
    claim_id = stable_id("cl", *natural, draft.conditions)
    fact = Claim(
        claim_id=claim_id,
        spec_ids=sorted(set(draft.spec_ids)),
        text=" ".join(natural),
        claim_type=draft.claim_type,
        conditions=draft.conditions,
        status="insufficient",
        status_reason="Proposed original-text claim; qualified support is computed by coverage",
    )
    old = claims.get(claim_id)
    if old is not None:
        if old.model_dump(exclude={"spec_ids"}) != fact.model_dump(exclude={"spec_ids"}):
            raise ValueError("Conflicting claim identity in extraction")
        fact = fact.model_copy(update={"spec_ids": sorted(set(old.spec_ids) | set(fact.spec_ids))})
    claims[claim_id] = fact
    for relation in draft.relations:
        if relation.evidence_key not in keys:
            repairs.append({"repair": "dropped_relation", "evidence_key": relation.evidence_key})
            continue
        quote = keys[relation.evidence_key][0]
        link = ClaimEvidenceLink(
            claim_id=claim_id,
            evidence_id=quote.evidence_id,
            relation=relation.relation,
            rationale=relation.rationale,
        )
        links[(claim_id, quote.evidence_id, relation.relation)] = link


def _observation(draft, keys, observations):
    if draft.evidence_key not in keys:
        raise ValueError("Observation references an unknown original quote")
    quote, block = keys[draft.evidence_key]
    if block.type == "table":
        if _normalized(draft.raw_value) not in cell_values(block.content):
            raise ValueError("Observation raw value must be one complete original table cell")
    elif draft.raw_value not in quote.quote_or_raw_content:
        raise ValueError("Observation raw value is absent from its original evidence")
    if draft.unit and draft.unit not in quote.quote_or_raw_content:
        raise ValueError("Observation unit is absent from original evidence")
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    suffix = re.escape(draft.unit) if draft.unit else ""
    numeric = re.fullmatch(
        rf"\s*({number})(?:\s*±\s*({number}))?\s*(?:{suffix})?\s*", draft.raw_value
    )
    if (
        block.type != "table"
        and numeric is not None
        and re.search(
            r"(?<![0-9A-Za-z_.+\-])" + re.escape(draft.raw_value) + r"(?![0-9A-Za-z_.])",
            quote.quote_or_raw_content,
        )
        is None
    ):
        raise ValueError("Observation raw value clips another original number")
    for mapping in (draft.row_key, draft.column_key):
        for value in mapping.values():
            for item in value if isinstance(value, list) else [value]:
                if (
                    isinstance(item, str)
                    and item.casefold() not in quote.quote_or_raw_content.casefold()
                ):
                    raise ValueError(
                        "Observation header/row label is absent from original evidence"
                    )
    context = draft.context | {
        "source_block": quote.quote_or_raw_content,
        "source_caption": block.caption,
        "source_notes": block.notes,
    }
    observation_id = stable_id("obs", quote.evidence_id, draft.row_key, draft.column_key)
    fact = QuantitativeObservation.model_validate(
        draft.model_dump(exclude={"evidence_key"})
        | {
            "observation_id": observation_id,
            "evidence_id": quote.evidence_id,
            "context": context,
            "value": numeric[1] if numeric else None,
            "uncertainty": numeric[2] if numeric else None,
        }
    )
    if observation_id in observations and observations[observation_id] != fact:
        raise ValueError("Conflicting values for one original table cell")
    observations[observation_id] = fact


async def extract(llm, *, source, fetched, parsed, plan, brief, block_ids, timeout_s=60):
    validate_original(fetched, parsed)
    if (
        not block_ids
        or len(set(block_ids)) != len(block_ids)
        or any(type(index) is not int or not 0 <= index < len(parsed.blocks) for index in block_ids)
    ):
        raise ValueError("Extraction requires distinct bounded original block indexes")
    spec_ids = {spec.spec_id for spec in plan.claim_specs}

    class ScopedOutput(ExtractionOutput):
        @model_validator(mode="after")
        def original_scope(self):
            found = materialize(
                self,
                source=source,
                fetched=fetched,
                parsed=parsed,
                spec_ids=spec_ids,
                block_ids=set(block_ids),
                report=False,
            )
            if self.evidence and not found["evidence"]:
                # Nothing proposed survived: worth one repair with the reasons.
                materialize(
                    self,
                    source=source,
                    fetched=fetched,
                    parsed=parsed,
                    spec_ids=spec_ids,
                    block_ids=set(block_ids),
                    strict=True,
                )
            return self

    context = {
        "brief": brief.model_dump(mode="json"),
        "section_plan": plan.model_dump(mode="json"),
        "original_blocks": [
            {"block_index": index, **parsed.blocks[index].model_dump(mode="json")}
            for index in block_ids
        ],
    }
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(
        examples=EXTRACTION_FEW_SHOTS,
        schema=json.dumps(ScopedOutput.model_json_schema(), ensure_ascii=False),
        context=json.dumps(context, ensure_ascii=False),
    )
    if len(prompt.encode()) > 96000:
        raise ValueError("Original extraction prompt exceeds its bound; select fewer whole blocks")
    output = await complete(
        llm, prompt, ScopedOutput, operation="research", timeout_s=timeout_s, drop_extra=True
    )
    return materialize(
        output,
        source=source,
        fetched=fetched,
        parsed=parsed,
        spec_ids=spec_ids,
        block_ids=set(block_ids),
    )
