"""CodeCrafter agent: controlled analysis execution via fixed templates.

Runs a fixed operation (comparison_matrix / pairwise_delta / plot / statistic /
aggregation) over compatible metrics through the CodeExecutionPort sandbox.
Templates are deterministic (no free-form LLM code); the chart type is a param
of the `plot` operation.
"""
from __future__ import annotations

import json
from typing import Any

from domain.ports import CodeExecutionPort
from domain.research.ids import stable_id

# Closed set of controlled operations (aligned with data-model.md).
OPERATIONS = {"comparison_matrix", "pairwise_delta", "plot", "statistic", "aggregation"}


async def analyze(
    metrics: dict[str, dict[str, Any]],
    execution: CodeExecutionPort,
    template: str = "comparison_matrix",
) -> dict[str, Any]:
    """Execute a fixed analysis template over compatible metrics.

    Args:
        metrics: id-keyed ComparableMetric dict (must be compatible).
        execution: CodeExecutionPort (sandbox).
        template: The fixed operation name (one of OPERATIONS).

    Returns:
        An AnalysisArtifact dict with operation, output, and execution_status.
    """
    if template not in OPERATIONS:
        template = "comparison_matrix"
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
    """Compile the fixed template into auditable Python for the sandbox."""
    payload = json.dumps({"operation": template, "metrics": metrics})
    return (
        "import json, pathlib\n"
        f"payload = json.loads({payload!r})\n"
        "op = payload['operation']\n"
        "metrics = list(payload['metrics'].values())\n"
        "vals = []\n"
        "for m in metrics:\n"
        "    v = m.get('value', '')\n"
        "    try:\n"
        "        vals.append(float(v))\n"
        "    except (TypeError, ValueError):\n"
        "        pass\n"
        "result = {}\n"
        "if op == 'pairwise_delta' and len(vals) >= 2:\n"
        "    result = {'delta': vals[0] - vals[1]}\n"
        "elif op == 'statistic' and vals:\n"
        "    result = {'mean': sum(vals) / len(vals), 'count': len(vals)}\n"
        "elif op == 'comparison_matrix':\n"
        "    result = {'rows': [{'method': m.get('evaluated_method'), 'value': m.get('value')} "
        "for m in metrics]}\n"
        "elif op == 'plot':\n"
        "    result = {'chart': 'grouped_bar', 'series': [{'name': m.get('evaluated_method'), "
        "'value': m.get('value')} for m in metrics]}\n"
        "elif op == 'aggregation':\n"
        "    result = {'rows': [{'method': m.get('evaluated_method'), "
        "'dataset': m.get('evaluation_context', {}).get('dataset_and_version'), "
        "'value': m.get('value')} for m in metrics]}\n"
        "pathlib.Path('/work/out/result.json').write_text(json.dumps(result))\n"
    )
