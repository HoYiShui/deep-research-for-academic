"""CodeCrafter agent: controlled analysis execution via fixed templates.

Runs a fixed operation (comparison_matrix / pairwise_delta / plot / statistic /
aggregation) over comparable metrics through the CodeExecutionPort sandbox.
"""
from __future__ import annotations

from typing import Any

from domain.ports import CodeExecutionPort
from domain.research.ids import stable_id


async def analyze(
    metrics: dict[str, dict[str, Any]],
    execution: CodeExecutionPort,
    template: str = "comparison_matrix",
) -> dict[str, Any]:
    """Execute a fixed analysis template over comparable metrics.

    Args:
        metrics: id-keyed ComparableMetric dict (must be compatible).
        execution: CodeExecutionPort (sandbox).
        template: The fixed operation name.

    Returns:
        An AnalysisArtifact dict with operation, output, and execution_status.
    """
    code = _template_code(template, metrics)
    output = await execution.execute(code, {"metrics": metrics})
    return {
        "artifact_id": stable_id("art", template),
        "section_id": "",
        "input_metric_ids": list(metrics.keys()),
        "input_evidence_ids": [],
        "operation": template,
        "code_or_recipe": code,
        "output": output,
        "execution_status": "completed" if output else "failed",
    }


def _template_code(template: str, metrics: dict[str, dict[str, Any]]) -> str:
    """Return the code/recipe for a fixed template (placeholders for the sandbox)."""
    if template == "pairwise_delta":
        return "compute pairwise delta between the first two methods"
    if template == "plot":
        return "render a chart of the metrics"
    if template == "statistic":
        return "compute a summary statistic over the metrics"
    if template == "aggregation":
        return "aggregate the metrics into a coverage table"
    return "build a comparison matrix over the metrics"
