"""DataAnalyst agent: normalize evaluation context and judge comparability."""

from __future__ import annotations

from typing import Any

from domain.ports import LLMPort
from domain.research.agents.base import call_llm, parse_json
from domain.research.ids import stable_id


def compare(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Judge comparability of two normalized metrics.

    Args:
        a, b: Metric dicts, each with an evaluation_context holding
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


async def analyze(observations: dict[str, dict[str, Any]], llm: LLMPort) -> dict[str, dict[str, Any]]:
    """Normalize observations into comparable metrics and judge comparability.

    Args:
        observations: id-keyed QuantitativeObservation dict (from scout).
        llm: LLMPort (evaluation-context normalization).

    Returns:
        A dict keyed by comparable_metric_id, each with evaluated_method,
        evaluation_context, value, unit, and comparability.
    """
    normalized = await _normalize(llm, observations)
    metrics: dict[str, dict[str, Any]] = {}
    for i, obs in enumerate(normalized):
        comparability = "compatible"
        for j, other in enumerate(normalized):
            if i == j:
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


async def _normalize(
    llm: LLMPort, observations: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Normalize raw observations into metric candidates via the LLM."""
    if not observations:
        return []
    judgment = parse_json(await call_llm(llm, _normalize_prompt(observations)))
    return judgment.get("metrics", [])


def _normalize_prompt(observations: dict[str, dict[str, Any]]) -> str:
    """Build the evaluation-context normalization prompt."""
    rows = "\n".join(
        f"- [{obs_id}] {o.get('row_key', '')} / {o.get('column_key', '')} = {o.get('value', '')}"
        for obs_id, o in list(observations.items())[:30]
    )
    return (
        "You are a research metrics analyst. Given quantitative observations "
        "extracted from papers, normalize each into a comparable metric with a "
        "normalized evaluation context (do not guess missing fields; leave them "
        'empty). Respond with JSON only:\n'
        '{"metrics": [{"evaluated_method": "...", "metric_definition": "...", '
        '"evaluation_context": {"dataset_and_version": "...", "metric": "...", '
        '"split_or_protocol": "..."}, "value": "...", "unit": "..."}]}\n\n'
        f"Observations:\n{rows}\n"
    )
