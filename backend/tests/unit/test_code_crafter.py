"""Unit tests for code_crafter.analyze."""

import pytest

from domain.research.agents import code_crafter


class _FakeExecution:
    async def execute(self, code, input_data, timeout_s=30):
        return {"result": "ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op", ["comparison_matrix", "pairwise_delta", "plot", "statistic", "aggregation"]
)
async def test_all_operations_produce_artifacts(op: str) -> None:
    artifact = await code_crafter.analyze({"m1": {"value": "0.9"}}, _FakeExecution(), op)
    assert artifact["operation"] == op
    assert artifact["input_metric_ids"] == ["m1"]
    assert artifact["execution_status"] == "completed"
    assert "code_or_recipe" in artifact


@pytest.mark.asyncio
async def test_unknown_operation_falls_back_to_comparison_matrix() -> None:
    artifact = await code_crafter.analyze({}, _FakeExecution(), "unknown_op")
    assert artifact["operation"] == "comparison_matrix"


@pytest.mark.asyncio
async def test_empty_output_is_failed() -> None:
    class _Empty:
        async def execute(self, code, input_data, timeout_s=30):
            return {}

    artifact = await code_crafter.analyze({}, _Empty())
    assert artifact["execution_status"] == "failed"
