"""One provider response, including measured usage; no adapter-owned retries."""

from typing import Protocol

from pydantic import StrictStr

from domain.research.models import Nonnegative, Record, Text


class ModelCompletion(Record):
    response_id: Text
    model: Text
    text: StrictStr
    stop_reason: Text
    input_tokens: Nonnegative
    output_tokens: Nonnegative

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class MeteredModelPort(Protocol):
    async def complete_metered(self, prompt: str) -> ModelCompletion: ...
