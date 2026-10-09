"""Bounded clarification repair and provider truncation must not mean success."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from domain.ports import AdapterError
from domain.research.agents import architect, structured
from domain.research.models import PartialResearchBrief, SourceSelection
from infrastructure.llm.deepseek import DeepSeekLLM
from tests.support.llm_stream import stream_via_create
from tests.unit.test_mono_clarify import assessment, core


async def invoke(model):
    return await architect.clarify(
        model,
        draft=PartialResearchBrief(),
        query="Public test evaluation",
        answer="Public test evaluation",
        source_selection=SourceSelection(),
        pending_questions=[],
        history=[],
    )


async def test_schema_repair_is_one_call_and_has_validation_context():
    model = SimpleNamespace(
        complete=AsyncMock(
            side_effect=['{"missing_fields":[]', json.dumps(assessment(brief_patch=core()))]
        )
    )
    result = await invoke(model)
    assert result.brief_patch.task_type == "evaluation_design"
    assert model.complete.await_count == 2
    assert "repair" in model.complete.call_args.args[0].lower()


async def test_two_invalid_outputs_fail_without_third_repair():
    model = SimpleNamespace(complete=AsyncMock(return_value="{}"))
    with pytest.raises(AdapterError, match="model_output_invalid"):
        await invoke(model)
    assert model.complete.await_count == 2


async def test_provider_truncation_consumes_the_single_schema_repair():
    model = SimpleNamespace(
        complete=AsyncMock(
            side_effect=[
                AdapterError("llm", "model_output_invalid", "Truncated", False, "complete"),
                json.dumps(assessment(brief_patch=core())),
            ]
        )
    )
    assert (await invoke(model)).brief_patch.task_type == "evaluation_design"
    assert model.complete.await_count == 2


async def test_total_calls_include_network_failure_and_schema_repair(monkeypatch):
    model = SimpleNamespace(
        complete=AsyncMock(
            side_effect=[
                OSError("private-provider"),
                "{}",
                json.dumps(assessment(brief_patch=core())),
            ]
        )
    )
    monkeypatch.setattr(structured, "_retry_pause", AsyncMock())
    assert (await invoke(model)).brief_patch.task_type == "evaluation_design"
    assert model.complete.await_count == 3


async def test_truncated_sdk_response_is_rejected_even_if_text_looks_valid():
    response = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(text=json.dumps(assessment()))],
    )
    with patch("infrastructure.llm.deepseek.AsyncAnthropic") as sdk:
        stream_via_create(sdk)
        sdk.return_value.messages.create = AsyncMock(return_value=response)
        model = DeepSeekLLM(retries=2, max_tokens=16384)
        with pytest.raises(AdapterError, match="model_output_invalid"):
            await model.complete("public test")
        assert sdk.return_value.messages.create.await_count == 1
        assert sdk.return_value.messages.create.call_args.kwargs["max_tokens"] == 16384
