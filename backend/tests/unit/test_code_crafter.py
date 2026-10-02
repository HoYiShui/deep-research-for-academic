"""Unit tests for code_crafter.analyze."""

import pytest

from domain.research.agents import code_crafter


class _FakeExecution:
    async def execute(self, code, input_data, timeout_s=30):
        return {"result": "ok"}


@pytest.mark.asyncio
async def test_analyze_returns_artifact() -> None:
    artifact = await code_crafter.analyze([], _FakeExecution(), "pairwise_delta")
    assert artifact["operation"] == "pairwise_delta"
    assert artifact["execution_status"] == "completed"
