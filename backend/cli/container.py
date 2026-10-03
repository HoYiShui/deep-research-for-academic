"""Build the service container (fake or real) from CLI flags."""

from __future__ import annotations

from application.bootstrap import Container
from infrastructure.fake import (
    FakeExecution,
    FakeLLM,
    FakeRetrieval,
    FakeSearch,
    FakeStateStore,
)


def build_container(*, fake: bool, seed: int | None) -> Container:
    """Assemble the container; ``fake=True`` wires seeded in-memory fakes."""
    if fake:
        return Container(
            llm=FakeLLM(seed=seed if seed is not None else 0),
            search=FakeSearch(),
            retrieval=FakeRetrieval(),
            execution=FakeExecution(),
            store=FakeStateStore(),
        )
    return Container()
