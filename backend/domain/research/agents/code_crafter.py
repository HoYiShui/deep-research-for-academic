"""CodeCrafter agent: controlled analysis execution via fixed templates.

Runs a fixed operation (comparison_matrix / pairwise_delta / plot) over
comparable metrics through the CodeExecutionPort sandbox.
"""
from __future__ import annotations

from typing import Any

from domain.ports import CodeExecutionPort


async def analyze(
    metrics: list[dict[str, Any]],
    execution: CodeExecutionPort,
    template: str = "comparison_matrix",
) -> dict[str, Any]:
    """Execute a fixed analysis template over comparable metrics.

    Args:
        metrics: ComparableMetric list (must be compatible).
        execution: CodeExecutionPort (sandbox).
        template: The fixed template name.

    Returns:
        An AnalysisArtifact dict with operation, output, and execution_status.
    """
    code = _template_code(template, metrics)
    output = await execution.execute(code, {"metrics": metrics})
    return {
        "operation": template,
        "output": output,
        "execution_status": "completed" if output is not None else "failed",
    }


def _template_code(template: str, metrics: list[dict[str, Any]]) -> str:
    """Return the code/recipe for a fixed template (placeholders for the sandbox)."""
    if template == "pairwise_delta":
        return "compute pairwise delta between the first two methods"
    if template == "plot":
        return "render a grouped bar chart of the metrics"
    return "build a comparison matrix over the metrics"
