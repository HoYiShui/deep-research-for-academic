"""Fact shapes do not prove real source retrieval or valid computation."""

import pytest
from pydantic import ValidationError

from domain.research.facts import (
    AnalysisArtifact,
    AnalysisRequirement,
    Location,
    QuantitativeObservation,
)


@pytest.mark.parametrize(
    "operation,parameters",
    [
        ("comparison_matrix", {"columns": ["value"]}),
        (
            "pairwise_delta",
            {"left_claim_spec_id": "a", "right_claim_spec_id": "b", "mode": "absolute"},
        ),
        (
            "plot",
            {
                "kind": "bar",
                "x_field": "evaluated_method",
                "y_field": "value",
                "include_uncertainty": False,
            },
        ),
        ("statistic", {"kind": "mean", "group_by": []}),
        ("aggregation", {"kind": "count", "group_by": []}),
    ],
)
def test_operation_parameters_are_closed(operation, parameters):
    data = {
        "requirement_id": "r1",
        "operation": operation,
        "claim_spec_ids": ["a", "b"],
        "required_context_fields": [],
        "parameters": parameters,
    }
    assert AnalysisRequirement(**data).operation == operation
    with pytest.raises(ValidationError):
        AnalysisRequirement(**(data | {"parameters": parameters | {"python": "exec anything"}}))


@pytest.mark.parametrize(
    "operation,output",
    [
        ("comparison_matrix", {"columns": ["value"], "rows": []}),
        (
            "pairwise_delta",
            {"left_metric_id": "m1", "right_metric_id": "m2", "value": "0.1", "unit": "%"},
        ),
        ("plot", {"points": [], "files": []}),
        ("statistic", {"groups": []}),
        ("aggregation", {"groups": []}),
    ],
)
def test_completed_artifact_output_is_operation_specific(operation, output):
    data = {
        "artifact_id": "a1",
        "section_id": "section_3",
        "input_metric_ids": [],
        "input_evidence_ids": [],
        "comparison_set_id": "g1",
        "operation": operation,
        "code_or_recipe": "controlled template",
        "template_version": "mono-v1",
        "output": output,
        "object_keys": [],
        "execution_status": "completed",
        "failure": None,
    }
    assert AnalysisArtifact(**data).operation == operation
    with pytest.raises(ValidationError):
        AnalysisArtifact(**(data | {"output": output | {"made_up": True}}))


def test_locations_and_decimal_observations_do_not_fake_known_values():
    for location in ({}, {"page_start": 3, "page_end": 2}, {"line_end": 10}, {"page_start": True}):
        with pytest.raises(ValidationError):
            Location(**location)
    data = {
        "observation_id": "o1",
        "evidence_id": "e1",
        "kind": "benchmark_result",
        "row_key": {"method": "fixture"},
        "column_key": {"metric": "accuracy"},
        "raw_value": "not reported",
        "value": None,
        "uncertainty": None,
        "statistic": "not reported",
        "context": {"dataset": None},
        "unit": None,
    }
    assert QuantitativeObservation(**data).value is None
    for change in ({"value": "NaN"}, {"value": 0.5}, {"context": {"nested": {"free": "object"}}}):
        with pytest.raises(ValidationError):
            QuantitativeObservation(**(data | change))
