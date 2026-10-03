"""Build the service container (fake or real) from CLI flags."""

from __future__ import annotations

from application.bootstrap import Container
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
    llm = FakeLLM(seed=seed if seed is not None else 0) if fake else DeepSeekLLM()
    llm = _VerboseLLM(llm, verbose) if verbose else llm
    if fake:
        return Container(
            llm=llm,
            search=FakeSearch(),
            retrieval=FakeRetrieval(),
            execution=FakeExecution(),
            store=FakeStateStore(),
        )
    return Container(llm=llm)
