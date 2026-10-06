"""Search candidates and request-local outcomes, never original-text Evidence."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, StrictBool, StrictStr, model_validator

from domain.research.facts import SourceTier
from domain.research.models import Positive, Record, Text


def public_url(value: str) -> str:
    """Syntax only; Fetch must separately resolve/guard every network hop."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid candidate URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(ord(char) < 33 or char == "\\" for char in value)
        or port == 0
    ):
        raise ValueError("Candidate URL must be HTTP(S) without credentials")
    return value


CandidateURL = Annotated[Text, Field(max_length=4096), AfterValidator(public_url)]
Metadata = Annotated[StrictStr, Field(max_length=4096)]


class SearchResult(Record):
    # Provider-local candidate ID, not a versioned SourceRecord identity.
    source_id: Annotated[Text, Field(max_length=4096)]
    source_type: Literal["paper", "web"]
    title: Annotated[Text, Field(max_length=4096)]
    snippet: Annotated[StrictStr, Field(max_length=20000)]
    url: CandidateURL
    authors_or_publisher: list[Annotated[Text, Field(max_length=1000)]] = Field(
        default_factory=list, max_length=100
    )
    published_at: Metadata = ""
    version: Metadata = ""
    provider: Annotated[Text, Field(max_length=100)] = "unknown"
    source_tier: SourceTier = "unknown"
    fulltext_url: CandidateURL | None = None


SearchFailureCode = Literal[
    "search_timeout",
    "search_unavailable",
    "search_rate_limited",
    "search_http_error",
    "search_not_configured",
    "search_response_invalid",
    "search_response_too_large",
    "search_provider_error",
]


class SearchFailure(Record):
    code: SearchFailureCode
    retryable: StrictBool


class SearchOutcome(Record):
    source: Annotated[Text, Field(max_length=100)]
    attempts: Annotated[Positive, Field(le=2)]
    status: Literal["ok", "empty", "failed"]
    items: list[SearchResult] = Field(default_factory=list, max_length=50)
    failure: SearchFailure | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "failed":
            if self.failure is None or self.items:
                raise ValueError("Failed source requires a failure and no candidates")
        elif self.failure is not None or bool(self.items) != (self.status == "ok"):
            raise ValueError("Successful source must distinguish empty from candidates")
        return self


class SearchBatch(Record):
    outcomes: list[SearchOutcome] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_sources(self):
        if len({outcome.source for outcome in self.outcomes}) != len(self.outcomes):
            raise ValueError("Search source names must be unique")
        return self

    @property
    def items(self) -> list[SearchResult]:
        return [item for outcome in self.outcomes for item in outcome.items]

    @property
    def all_failed(self) -> bool:
        return all(outcome.status == "failed" for outcome in self.outcomes)
