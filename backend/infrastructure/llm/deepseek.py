"""DeepSeek LLM adapter (Anthropic-compatible API)."""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic


class DeepSeekLLM:
    """LLMPort implementation via the Anthropic SDK pointed at DeepSeek."""

    def __init__(self) -> None:
        self._client = AsyncAnthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
        self._model = os.environ.get("ANTHROPIC_MODEL", "deepseek-chat")

    async def complete(self, prompt: str) -> str:
        """Call the LLM and return its text output."""
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
