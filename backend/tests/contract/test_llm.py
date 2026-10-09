"""Contract test for LLMPort (DeepSeek) using a mocked client."""

from unittest.mock import AsyncMock, patch

import pytest

from domain.ports import LLMPort
from infrastructure.llm.deepseek import DeepSeekLLM
from tests.support.llm_stream import stream_via_create


class _FakeMessage:
    text = "hello from deepseek"


class _FakeContent:
    content = [_FakeMessage()]


@pytest.mark.asyncio
async def test_deepseek_complete_returns_text() -> None:
    with patch("infrastructure.llm.deepseek.AsyncAnthropic") as mock_cls:
        stream_via_create(mock_cls)
        mock_cls.return_value.messages.create = AsyncMock(return_value=_FakeContent())
        llm: LLMPort = DeepSeekLLM()
        assert await llm.complete("hi") == "hello from deepseek"
