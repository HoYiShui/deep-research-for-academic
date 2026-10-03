"""DataAnalyst agent: normalize evaluation context and judge comparability."""

from __future__ import annotations

from typing import Any

from domain.research.ids import stable_id


def compare(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Judge comparability of two observations.

    Args:
        a, b: Observation dicts, each with an evaluation_context holding
            dataset_and_version, metric, and split_or_protocol.

    Returns:
        "compatible" (same dataset+metric+protocol), "partial" (same
        dataset+metric but different protocol), or "incompatible" (different
        dataset or metric).
    """
    ca = a.get("evaluation_context", {})
    cb = b.get("evaluation_context", {})
    if ca.get("dataset_and_version") != cb.get("dataset_and_version") or ca.get("metric") != cb.get(
        "metric"
    ):
        return "incompatible"
    if ca.get("split_or_protocol") != cb.get("split_or_protocol"):
        return "partial"
    return "compatible"


def analyze(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Produce id-keyed ComparableMetric dict with comparability flags.

    Args:
        observations: Observation dicts.

    Returns:
        A dict keyed by comparable_metric_id, each with evaluated_method,
        evaluation_context, and comparability (compatible/partial/incompatible).
    """
    metrics: dict[str, dict[str, Any]] = {}
    for obs in observations:
        comparability = "compatible"
        for other in observations:
            if other is obs:
                continue
            c = compare(obs, other)
            if c == "incompatible":
                comparability = "incompatible"
                break
            if c == "partial":
                comparability = "partial"
        evaluated_method = obs.get("evaluated_method", "")
        metric_id = stable_id("cm", evaluated_method, comparability)
        metrics[metric_id] = {
            "comparable_metric_id": metric_id,
            "observation_ids": obs.get("observation_ids", []),
            "metric_definition": obs.get("metric_definition", ""),
            "evaluated_method": evaluated_method,
            "evaluation_context": obs.get("evaluation_context", {}),
            "value": obs.get("value", ""),
            "unit": obs.get("unit", ""),
            "comparability": comparability,
            "reasons": [],
        }
    return metrics
