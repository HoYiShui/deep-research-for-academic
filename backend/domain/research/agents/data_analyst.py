"""DataAnalyst agent: normalize evaluation context and judge comparability."""

from __future__ import annotations

from typing import Any

from domain.ports import LLMPort
from domain.research.agents.base import call_llm, parse_json
from domain.research.ids import stable_id


def assess_requirement(plan, requirement, values):
    """Conservative normalization of registered observations, never invented values.

    Numerical execution is deliberately unavailable in this first web workflow.
    Its absence becomes a Gap, not a fabricated completed Artifact.
    """
    from domain.research.facts import ComparableMetric, ComparisonSet, Gap, SectionCoverage

    related = {
        link.evidence_id
        for link in values["claim_evidence_links"]
        if set(values["claims"][link.claim_id].spec_ids) & set(requirement.claim_spec_ids)
    }
    required = list(
        dict.fromkeys(
            [
                "task",
                "dataset_and_version",
                "split_or_protocol",
                "metric_definition",
                "unit",
                *requirement.required_context_fields,
            ]
        )
    )
    metrics = {}
    for observation in values["quantitative_observations"].values():
        if observation.evidence_id not in related:
            continue
        context = dict(observation.context)
        if observation.unit is not None:
            context["unit"] = observation.unit
        missing = [key for key in required if context.get(key) in (None, "", [])]
        key = stable_id("metric", observation.observation_id, context)
        metrics[key] = ComparableMetric(
            comparable_metric_id=key,
            observation_ids=[observation.observation_id],
            metric_definition=str(context.get("metric_definition") or "原文未提供指标定义"),
            evaluated_method=str(context.get("evaluated_method") or "原文未提供方法标识"),
            evaluation_context=context,
            value=observation.value,
            unit=observation.unit,
            normalization_basis="仅保留已登记 Observation 的值、单位与上下文；未换算或填补缺项",
            missing_context_fields=missing,
        )
    reasons = []
    if not metrics:
        reasons.append("该分析要求没有可用的原文数值观察")
    elif any(metric.missing_context_fields for metric in metrics.values()):
        reasons.append("原文缺少比较所需条件；两个未知条件也不能视为相同")
    elif any(metric.value is None for metric in metrics.values()):
        reasons.append("原文数值不能可靠解析，保留原文但不进入数值计算")
    elif requirement.operation == "pairwise_delta" and len(metrics) < 2:
        reasons.append("两两差值需要两个已登记观察，单项数值不能构成比较")
    else:
        first = next(iter(metrics.values()))
        for metric in metrics.values():
            if any(
                metric.evaluation_context.get(key) != first.evaluation_context.get(key)
                for key in required
            ):
                reasons.append("指标所属数据、协议、定义或其他必需条件不同，不作跨条件排名")
                break
    compatible = not reasons
    group_id = stable_id("comparison", plan.section_id, requirement.requirement_id, list(metrics))
    group = ComparisonSet(
        comparison_set_id=group_id,
        section_id=plan.section_id,
        requirement_id=requirement.requirement_id,
        metric_ids=list(metrics),
        required_context_fields=required,
        comparability="compatible" if compatible else "incompatible",
        reasons=reasons or ["已登记原文条件一致；计算执行能力尚未接入当前调试链路"],
    )
    reason = (
        "；".join(reasons)
        if reasons
        else "条件可比，但当前调试链路未接入受控数值计算；没有生成计算结果"
    )
    coverage = values["section_coverage"][plan.section_id]
    gap = Gap(
        gap_id=stable_id("analysis_gap", group_id, reason),
        section_id=plan.section_id,
        claim_spec_id=requirement.claim_spec_ids[0],
        claim_id=None,
        reason=reason,
        fillable=False,
        verification_action="补齐原文数值及同协议条件，并通过受控分析模板验证后再引用计算结论",
    )
    gaps = {item.gap_id: item for item in [*coverage.gaps, gap]}
    return {
        "comparable_metrics": metrics,
        "comparison_sets": {group_id: group},
        "analysis_artifacts": {},
        "section_coverage": {
            plan.section_id: SectionCoverage.model_validate(
                coverage.model_dump() | {"gaps": list(gaps.values())}
            )
        },
    }


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


async def analyze(
    observations: dict[str, dict[str, Any]], llm: LLMPort
) -> dict[str, dict[str, Any]]:
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


async def _normalize(llm: LLMPort, observations: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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
        "empty). Respond with JSON only:\n"
        '{"metrics": [{"evaluated_method": "...", "metric_definition": "...", '
        '"evaluation_context": {"dataset_and_version": "...", "metric": "...", '
        '"split_or_protocol": "..."}, "value": "...", "unit": "..."}]}\n\n'
        f"Observations:\n{rows}\n"
    )
