"""Canonical worker adapters. Tool callbacks own budget/cache/protocol I/O."""

import json
import re
from datetime import UTC, datetime

from application.errors import AppError
from domain.documents import FetchedDocument, ParsedDocument
from domain.ports import AdapterError
from domain.research.agents import architect
from domain.research.agents.coverage import section_coverage
from domain.research.agents.extraction import extract
from domain.research.agents.originals import register_original
from domain.research.ids import canonical_hash
from domain.research.phase_contracts import PhaseResult
from domain.research.search import SearchBatch


class _ContextLLM:
    def __init__(self, context, phase):
        self.context, self.phase = context, phase

    async def complete(self, prompt):
        result = await self.context.invoke("llm", {"phase": self.phase, "prompt": prompt})
        if not isinstance(result, str):
            raise AdapterError(
                "llm",
                "model_output_invalid",
                "Tool completion has an invalid shape",
                False,
                self.phase,
            )
        return result


async def plan_worker(value, context):
    plans = await architect.plan(
        _ContextLLM(context, "plan"),
        value.values["research_brief"],
        source_selection=value.values["source_selection"],
        timeout_s=context.config.timeouts_s.llm,
    )
    return PhaseResult(
        phase="plan",
        unit_id=context.unit_id,
        input_hash=value.semantic_hash,
        changes={"section_plans": plans},
        degradations=[],
        failures=[],
    )


def _blocks(parsed, plan, query):
    terms = set(
        re.findall(
            r"[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}",
            " ".join([query, *plan.retrieval_anchors]).casefold(),
        )
    )
    ranked = sorted(
        range(len(parsed.blocks)),
        key=lambda index: (
            -sum(term in parsed.blocks[index].content.casefold() for term in terms),
            index,
        ),
    )
    selected, size = [], 0
    for index in ranked:
        cost = len(
            json.dumps(parsed.blocks[index].model_dump(mode="json"), ensure_ascii=False).encode()
        )
        if size + cost <= 32000 and len(selected) < 12:
            selected.append(index)
            size += cost
    return sorted(selected)


async def research_worker(value, context):
    unit = context.unit
    if (
        unit is None
        or len(unit.section_ids) != 1
        or unit.parameters.get("kind") not in {"query", "coverage"}
    ):
        raise AppError("invalid_state", "Research worker requires one explicit query/coverage unit")
    plan = next(
        (plan for plan in value.values["section_plans"] if plan.section_id == unit.section_ids[0]),
        None,
    )
    if plan is None:
        raise AppError("invalid_state", "Research unit has no section plan")
    if "knowledge_base" in value.values["source_selection"].categories:
        raise AppError("service_not_ready", "Authorized knowledge retrieval is not yet configured")
    changes = {name: {} for name in ("sources", "evidence", "claims", "quantitative_observations")}
    changes["claim_evidence_links"] = []
    degradations = []

    def degraded(source, reason, operation):
        degradations.append(
            {
                "source": source,
                "reason": reason,
                "operation": operation,
                "section_id": plan.section_id,
                "occurred_at": datetime.now(UTC),
            }
        )

    if unit.parameters["kind"] == "query":
        query = unit.parameters["query"]
        batch = SearchBatch.model_validate(await context.invoke("search", {"query": query}))
        if batch.all_failed:
            raise AdapterError(
                "search",
                "all_search_sources_failed",
                "All selected search providers failed",
                True,
                "research",
            )
        for outcome in batch.outcomes:
            if outcome.status == "failed":
                degraded(outcome.source, outcome.failure.code, "search")
        selected, targets = [], set()
        for candidate in batch.items:
            target = candidate.fulltext_url if candidate.source_type == "paper" else candidate.url
            if target not in targets and len(selected) < 4:
                selected.append(candidate)
                targets.add(target)
        if len(selected) < len(
            {candidate.fulltext_url or candidate.url for candidate in batch.items}
        ):
            degraded(
                "research",
                "Additional candidates remain unexamined beyond the bounded selection",
                "candidate_selection",
            )
        for candidate in selected:
            if await context.cancel_check():
                raise AppError("invalid_session_state", "Research execution is stopping")
            try:
                original = await context.invoke(
                    "fetch", {"candidate_key": canonical_hash(candidate)}
                )
            except AdapterError as failure:
                if failure.operation not in {"fetch", "parse"}:
                    raise  # Cache/integrity/control failures cannot become search gaps.
                if failure.code in {
                    "content_scope_mismatch",
                    "content_hash_mismatch",
                    "content_invalid",
                }:
                    raise
                degraded(candidate.source_id, failure.code, "fetch")
                continue
            fetched, parsed = (
                FetchedDocument.model_validate(original["fetched"]),
                ParsedDocument.model_validate(original["parsed"]),
            )
            source = register_original(candidate, fetched, parsed, retrieved_at=datetime.now(UTC))
            blocks = _blocks(parsed, plan, query)
            if len(blocks) < len(parsed.blocks):
                degraded(
                    source.source_id,
                    "Some whole original blocks were not examined within extraction limits",
                    "block_selection",
                )
            if not blocks or not plan.claim_specs:
                changes["sources"][source.source_id] = source
                continue
            facts = await extract(
                _ContextLLM(context, "research"),
                source=source,
                fetched=fetched,
                parsed=parsed,
                plan=plan,
                brief=value.values["research_brief"],
                block_ids=blocks,
                timeout_s=context.config.timeouts_s.llm,
            )
            for name in ("sources", "evidence", "claims", "quantitative_observations"):
                for key, fact in facts[name].items():
                    old = changes[name].get(key) or value.values[name].get(key)
                    if old is not None and name == "sources":
                        if (old.content_hash, old.content_object_key) != (
                            fact.content_hash,
                            fact.content_object_key,
                        ):
                            raise AppError(
                                "invalid_state", "Versioned source has conflicting original bytes"
                            )
                        fact = old.model_copy(
                            update={"provenance": [*old.provenance, *fact.provenance]}
                        )
                    if old is not None and name == "claims":
                        fact = fact.model_copy(
                            update={"spec_ids": sorted(set(old.spec_ids) | set(fact.spec_ids))}
                        )
                    if (
                        old is not None
                        and name in {"evidence", "quantitative_observations"}
                        and old != fact
                    ):
                        raise AppError(
                            "invalid_state", "Original extraction conflicts with a stable fact"
                        )
                    changes[name][key] = fact
            changes["claim_evidence_links"].extend(facts["claim_evidence_links"])
    combined = {
        name: value.values[name] | changes[name] for name in ("sources", "evidence", "claims")
    }
    updates, coverage = section_coverage(
        plan,
        combined["claims"],
        combined["evidence"],
        combined["sources"],
        [*value.values["claim_evidence_links"], *changes["claim_evidence_links"]],
        all_specs={
            spec.spec_id: spec
            for item in value.values["section_plans"]
            for spec in item.claim_specs
        },
    )
    changes["claims"].update(updates)
    changes["section_coverage"] = {plan.section_id: coverage}
    return PhaseResult(
        phase="research",
        unit_id=context.unit_id,
        input_hash=value.semantic_hash,
        changes=changes,
        degradations=degradations,
        failures=[],
    )
