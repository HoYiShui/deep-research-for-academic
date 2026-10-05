"""Build the service container (fake or real) from CLI flags."""

from __future__ import annotations

from application.bootstrap import Container
from application.settings import Settings
from cli import output
from infrastructure.fake import (
    FakeExecution,
    FakeLLM,
    FakeRetrieval,
    FakeSearch,
    FakeStateStore,
)
from infrastructure.llm.deepseek import DeepSeekLLM


class _VerboseLLM:
    """Wrap an LLM to log each prompt/response to stderr (--verbose)."""

    def __init__(self, inner, verbose: bool) -> None:
        self._inner = inner
        self._verbose = verbose

    async def complete(self, prompt: str) -> str:
        if self._verbose:
            output.log(f"LLM prompt: {prompt[:300]}")
        result = await self._inner.complete(prompt)
        if self._verbose:
            output.log(f"LLM response: {result[:300]}")
        return result


def build_container(*, fake: bool, seed: int | None, verbose: bool = False) -> Container:
    """Assemble the container; ``fake=True`` wires seeded in-memory fakes."""
    settings = Settings.load()
    llm = FakeLLM(seed=seed if seed is not None else 0) if fake else DeepSeekLLM(
        api_key=settings.anthropic_api_key.get_secret_value(),
        base_url=settings.anthropic_base_url, model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    llm = _VerboseLLM(llm, verbose) if verbose else llm
    if fake:
        return Container(
            settings=settings,
            llm=llm,
            search=FakeSearch(),
            retrieval=FakeRetrieval(),
            execution=FakeExecution(),
            store=FakeStateStore(),
        )
    return Container(llm=llm, settings=settings)
