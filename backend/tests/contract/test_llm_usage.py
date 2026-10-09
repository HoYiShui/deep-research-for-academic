"""Provider metadata contract, not a live/paid model verification."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from domain.ports import AdapterError
from infrastructure.llm.deepseek import DeepSeekLLM
from tests.support.llm_stream import stream_via_create


def message(index=1, **changes):
    return SimpleNamespace(
        **{
            "id": f"msg_{index}",
            "model": "provider-alias",
            "content": [SimpleNamespace(thinking="ignored"), SimpleNamespace(text=f"text_{index}")],
            "stop_reason": "end_turn",
            "usage": SimpleNamespace(input_tokens=index * 10, output_tokens=index * 2),
            **changes,
        }
    )


async def test_metered_single_attempt_keeps_usage_even_if_truncated():
    with patch("infrastructure.llm.deepseek.AsyncAnthropic") as sdk:
        stream_via_create(sdk)
        sdk.return_value.messages.create = AsyncMock(return_value=message(stop_reason="max_tokens"))
        llm = DeepSeekLLM(retries=2)
        result = await llm.complete_metered("question")
        assert result.text == "text_1" and result.total_tokens == 12
        assert result.stop_reason == "max_tokens"
        assert sdk.return_value.messages.create.await_count == 1


@pytest.mark.parametrize(
    "usage",
    [
        None,
        SimpleNamespace(input_tokens=True, output_tokens=1),
        SimpleNamespace(input_tokens=-1, output_tokens=1),
        SimpleNamespace(input_tokens="12", output_tokens=1),
    ],
)
async def test_missing_or_invalid_usage_is_not_zero_spend(usage):
    with patch("infrastructure.llm.deepseek.AsyncAnthropic") as sdk:
        stream_via_create(sdk)
        sdk.return_value.messages.create = AsyncMock(return_value=message(usage=usage))
        with pytest.raises(AdapterError) as failure:
            await DeepSeekLLM().complete_metered("question")
        assert failure.value.code == "model_usage_invalid"
        assert sdk.return_value.messages.create.await_count == 1


async def test_concurrent_results_have_request_local_usage():
    with patch("infrastructure.llm.deepseek.AsyncAnthropic") as sdk:
        stream_via_create(sdk)

        async def complete(**args):
            index = int(args["messages"][0]["content"])
            await asyncio.sleep(0)
            return message(index)

        sdk.return_value.messages.create = AsyncMock(side_effect=complete)
        llm = DeepSeekLLM()
        results = await asyncio.gather(llm.complete_metered("1"), llm.complete_metered("2"))
        assert [result.total_tokens for result in results] == [12, 24]
        assert [result.response_id for result in results] == ["msg_1", "msg_2"]


async def test_metered_error_has_no_adapter_retry():
    with patch("infrastructure.llm.deepseek.AsyncAnthropic") as sdk:
        stream_via_create(sdk)
        sdk.return_value.messages.create = AsyncMock(side_effect=TimeoutError())
        with pytest.raises(TimeoutError):
            await DeepSeekLLM(retries=2).complete_metered("question")
        assert sdk.return_value.messages.create.await_count == 1


async def test_metered_cancellation_propagates():
    with patch("infrastructure.llm.deepseek.AsyncAnthropic") as sdk:
        stream_via_create(sdk)
        sdk.return_value.messages.create = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await DeepSeekLLM().complete_metered("question")
        assert sdk.return_value.messages.create.await_count == 1
