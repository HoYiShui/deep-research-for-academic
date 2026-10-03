"""DeepSeek LLM adapter (Anthropic-compatible API) with retry/backoff.

Failure semantics (plan.md): a transient call failure retries twice with
exponential backoff; exhaustion raises so the orchestrator terminates the
step and persists the error.
"""

from __future__ import annotations

import asyncio
import os

from anthropic import AsyncAnthropic


class DeepSeekLLM:
    """LLMPort implementation via the Anthropic SDK pointed at DeepSeek."""

    def __init__(self, retries: int = 2, backoff: float = 0.5) -> None:
        self._client = AsyncAnthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"),
        )
        self._model = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
        self._retries = retries
        self._backoff = backoff

    async def complete(self, prompt: str) -> str:
        """Call the LLM, retrying transient failures with exponential backoff.

        Args:
            prompt: The prompt to send.

        Returns:
            The assistant's text output.

        Raises:
            The last exception after retries are exhausted.
        """
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                return await self._complete_once(prompt)
            except Exception as exc:  # noqa: BLE001 — retry any transient failure
                last_exc = exc
                if attempt < self._retries:
                    await asyncio.sleep(self._backoff * (2**attempt))
        assert last_exc is not None
        raise last_exc

    async def _complete_once(self, prompt: str) -> str:
        """Perform a single (non-retried) completion call."""
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        # Some models emit thinking blocks alongside text; keep only the text.
        return "".join(getattr(block, "text", "") for block in response.content)
