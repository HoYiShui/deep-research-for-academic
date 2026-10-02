"""Unit tests for data_analyst.comparability."""

from domain.research.agents.data_analyst import compare


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
