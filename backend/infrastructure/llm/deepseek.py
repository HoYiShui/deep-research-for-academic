"""DeepSeek LLM adapter (Anthropic-compatible API) with retry/backoff.

Failure semantics (plan.md): a transient call failure retries twice with
exponential backoff; exhaustion raises so the orchestrator terminates the
step and persists the error.
"""

from __future__ import annotations

import asyncio
import os

from anthropic import AsyncAnthropic
from pydantic import ValidationError

from domain.model_completion import ModelCompletion
from domain.ports import AdapterError


class DeepSeekLLM:
    """LLMPort implementation via the Anthropic SDK pointed at DeepSeek."""

    def __init__(
        self,
        retries: int = 2,
        backoff: float = 0.5,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 60,
        max_tokens: int = 4096,
    ) -> None:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("Output token limit must be a positive integer")
        self._client = AsyncAnthropic(
            api_key=api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=base_url
            or os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"),
            timeout=timeout_s,
            max_retries=0,
        )
        self._model = model or os.environ.get("LLM_MODEL", "deepseek-flash")
        self._retries = retries
        self._backoff = backoff
        self._max_tokens = max_tokens

    async def aclose(self) -> None:
        await self._client.close()

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
            except Exception as exc:
                if isinstance(exc, AdapterError) and not exc.retryable:
                    raise
                last_exc = exc
                if attempt < self._retries:
                    await asyncio.sleep(self._backoff * (2**attempt))
        assert last_exc is not None
        raise last_exc

    async def _complete_once(self, prompt: str) -> str:
        """Perform a single (non-retried) completion call."""
        response = await self._request(prompt)
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise AdapterError(
                "llm",
                "model_output_invalid",
                "Model output reached its token limit",
                False,
                "complete",
            )
        # Some models emit thinking blocks alongside text; keep only the text.
        return "".join(getattr(block, "text", "") for block in response.content)

    async def _request(self, prompt: str):
        return await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

    async def complete_metered(self, prompt: str) -> ModelCompletion:
        """Exactly one attempt; coordinator owns reservations and retries.

        A truncated response still carries its measured usage. The coordinator
        must record that spend before rejecting/repairing the model output.
        Missing usage is an error, not zero tokens; no mutable last_usage field
        is shared between concurrent calls.
        """
        response = await self._request(prompt)
        usage = getattr(response, "usage", None)
        try:
            return ModelCompletion(
                response_id=getattr(response, "id", None),
                model=getattr(response, "model", None),
                text="".join(getattr(block, "text", "") for block in response.content),
                stop_reason=getattr(response, "stop_reason", None),
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
            )
        except (ValidationError, AttributeError, TypeError):
            raise AdapterError(
                "llm",
                "model_usage_invalid",
                "Provider response has invalid usage metadata",
                False,
                "complete_metered",
            ) from None
