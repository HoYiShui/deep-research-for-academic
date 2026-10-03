"""Unit tests for data_analyst.comparability and analyze."""

import pytest

from domain.research.agents import data_analyst
from domain.research.agents.data_analyst import compare
from infrastructure.fake import FakeLLM


def _obs(dataset: str, metric: str, split: str) -> dict:
    return {
        "evaluation_context": {
            "dataset_and_version": dataset,
            "metric": metric,
            "split_or_protocol": split,
        }
    }


def test_same_context_is_compatible() -> None:
    assert compare(_obs("r4.2", "ACC", "temporal"), _obs("r4.2", "ACC", "temporal")) == "compatible"


def test_different_protocol_is_partial() -> None:
    assert compare(_obs("r4.2", "ACC", "temporal"), _obs("r4.2", "ACC", "random")) == "partial"


def test_different_dataset_is_incompatible() -> None:
    assert compare(_obs("r4.2", "ACC", "temporal"), _obs("r6.2", "ACC", "temporal")) == "incompatible"


def test_different_metric_is_incompatible() -> None:
    assert compare(_obs("r4.2", "ACC", "temporal"), _obs("r4.2", "F1", "temporal")) == "incompatible"


@pytest.mark.asyncio
async def test_analyze_normalizes_and_flags_incomparable() -> None:
    llm = FakeLLM(
        response='{"metrics": ['
        '{"evaluated_method": "A", "evaluation_context": {"dataset_and_version": "r4.2", '
        '"metric": "ACC", "split_or_protocol": "temporal"}},'
        '{"evaluated_method": "B", "evaluation_context": {"dataset_and_version": "r6.2", '
        '"metric": "ACC", "split_or_protocol": "temporal"}}]}'
    )
    metrics = await data_analyst.analyze({"o1": {}, "o2": {}}, llm)
    assert len(metrics) == 2
    for metric in metrics.values():
        assert metric["comparability"] == "incompatible"


@pytest.mark.asyncio
async def test_analyze_empty_observations() -> None:
    assert await data_analyst.analyze({}, FakeLLM()) == {}
