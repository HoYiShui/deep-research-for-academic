"""Model proposals cannot own IDs, quote locations, or invented table values."""

import copy
import json

import pytest

from domain.ports import AdapterError
from domain.research.agents.extraction import ExtractionOutput, extract, materialize
from domain.research.facts import SectionPlan
from tests.unit.test_phase_contracts import plans
from tests.unit.test_scout_originals import original
from tests.unit.test_state import initial_state


def proposal(parsed):
    block = parsed.blocks[0]
    return {
        "evidence": [
            {
                "key": "quote-1",
                "block_index": 0,
                "quote": "\n".join([block.content, block.caption, *block.notes]),
                "evidence_type": "result_table",
            }
        ],
        "claims": [
            {
                "subject": "A",
                "predicate": "has",
                "object": "observed accuracy",
                "spec_ids": ["spec-1"],
                "claim_type": "factual",
                "conditions": {"split": "held-out"},
                "relations": [
                    {
                        "evidence_key": "quote-1",
                        "relation": "supports",
                        "rationale": "Recorded table cell",
                    }
                ],
            }
        ],
        "observations": [
            {
                "evidence_key": "quote-1",
                "kind": "benchmark_result",
                "row_key": {"method": "A"},
                "column_key": {"metric": "Accuracy"},
                "raw_value": "0",
                "value": "0",
                "uncertainty": None,
                "statistic": "reported",
                "context": {},
                "unit": "%",
            }
        ],
    }


def facts(value, source, fetched, parsed):
    return materialize(
        ExtractionOutput.model_validate(value),
        source=source,
        fetched=fetched,
        parsed=parsed,
        spec_ids={"spec-1"},
        block_ids={0},
    )


def test_zero_table_context_and_ids_are_original_bound():
    _, fetched, parsed, source = original(table=True)
    found = facts(proposal(parsed), source, fetched, parsed)
    claim = next(iter(found["claims"].values()))
    observation = next(iter(found["quantitative_observations"].values()))
    assert claim.status == "insufficient"  # not model-selected supported.
    assert observation.value == "0" and observation.uncertainty is None
    assert "Accuracy (%)" in observation.context["source_block"]
    assert observation.context["source_caption"] == parsed.blocks[0].caption
    assert observation.context["source_notes"] == parsed.blocks[0].notes
    assert observation.evidence_id in found["evidence"]


def test_claim_id_tracks_conditions_but_not_spec_membership_or_field_order():
    _, fetched, parsed, source = original(table=True)
    value = proposal(parsed)
    first = next(iter(facts(value, source, fetched, parsed)["claims"]))
    changed = copy.deepcopy(value)
    changed["claims"][0]["conditions"]["split"] = "cross-validation"
    assert next(iter(facts(changed, source, fetched, parsed)["claims"])) != first
    changed = copy.deepcopy(value)
    changed["claims"][0]["subject"] = "  A  "
    assert next(iter(facts(changed, source, fetched, parsed)["claims"])) == first


def test_literal_zero_is_retained_even_if_model_omits_decimal_value():
    _, fetched, parsed, source = original(table=True)
    value = proposal(parsed)
    value["observations"][0]["value"] = None
    result = facts(value, source, fetched, parsed)
    assert next(iter(result["quantitative_observations"].values())).value == "0"


@pytest.mark.parametrize(
    "change",
    [
        "quote",
        "block",
        "spec",
        "relation",
        "value",
        "uncertainty",
        "unit",
        "header",
        "duplicate_quote",
    ],
)
def test_invalid_model_fact_proposals_rejected(change):
    _, fetched, parsed, source = original(table=True)
    value = proposal(parsed)
    if change == "quote":
        value["evidence"][0]["quote"] = "A | 0"
    if change == "block":
        value["evidence"][0]["block_index"] = 99
    if change == "spec":
        value["claims"][0]["spec_ids"] = ["outside-chapter"]
    if change == "relation":
        value["claims"][0]["relations"][0]["evidence_key"] = "invented"
    if change == "value":
        value["observations"][0]["value"] = "0.99"
    if change == "uncertainty":
        value["observations"][0]["uncertainty"] = "0.1"
    if change == "unit":
        value["observations"][0]["unit"] = "milliseconds"
    if change == "header":
        value["observations"][0]["column_key"] = {"metric": "Precision"}
    if change == "duplicate_quote":
        value["evidence"].append(value["evidence"][0])
    with pytest.raises(ValueError):
        facts(value, source, fetched, parsed)


class Model:
    def __init__(self, outputs):
        self.outputs, self.prompts = outputs, []

    async def complete(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(self.outputs[min(len(self.prompts) - 1, len(self.outputs) - 1)])


@pytest.mark.parametrize("repair", [True, False])
async def test_bad_original_quote_gets_one_bounded_repair_then_explicit_failure(repair):
    _, fetched, parsed, source = original(table=True)
    valid = proposal(parsed)
    invalid = copy.deepcopy(valid)
    invalid["evidence"][0]["quote"] = "A | 0"
    model = Model([invalid, valid] if repair else [invalid])
    arguments = {
        "source": source,
        "fetched": fetched,
        "parsed": parsed,
        "plan": SectionPlan.model_validate(plans()[0]),
        "brief": initial_state().research_brief,
        "block_ids": [0],
    }
    if repair:
        found = await extract(model, **arguments)
        assert found["quantitative_observations"]
    else:
        with pytest.raises(AdapterError, match="model_output_invalid"):
            await extract(model, **arguments)
    assert len(model.prompts) == 2
    assert "validation_errors" in model.prompts[1]
