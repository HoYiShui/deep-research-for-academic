"""DataAnalyst agent: normalize evaluation context and judge comparability."""

from __future__ import annotations

from typing import Any


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


def analyze(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Produce ComparableMetric list with comparability flags.

    Args:
        observations: Observation dicts.

    Returns:
        A list of dicts with evaluated_method, evaluation_context, and
        comparability (compatible/partial/incompatible).
    """
    metrics: list[dict[str, Any]] = []
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
        metrics.append(
            {
                "evaluated_method": obs.get("evaluated_method", ""),
                "evaluation_context": obs.get("evaluation_context", {}),
                "comparability": comparability,
            }
        )
    return metrics
