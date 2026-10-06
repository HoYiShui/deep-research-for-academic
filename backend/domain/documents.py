"""Versioned parser outputs shared by ingestion and external research fetching."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StrictStr, model_validator

from domain.content import ContentRef
from domain.research.facts import Location
from domain.research.models import Hash, Positive, Record, Text
from domain.research.search import CandidateURL


class ParserConfig(Record):
    parser_version: Text
    max_pages: Annotated[Positive, Field(le=500)] = 500
    max_blocks: Annotated[Positive, Field(le=10000)] = 10000
    max_content_bytes: Annotated[Positive, Field(le=50 * 1024 * 1024)] = 50 * 1024 * 1024


def _visible_content(text: str) -> str:
    if not text.strip():
        raise ValueError("Parsed block must contain visible content")
    return text


class ParsedBlock(Record):
    type: Literal["text", "table", "formula"]
    content: Annotated[
        StrictStr,
        Field(min_length=1, max_length=2 * 1024 * 1024),
        AfterValidator(_visible_content),
    ]
    location: Location
    caption: Text | None = None
    notes: list[Text] = Field(default_factory=list, max_length=100)


class ParsedDocument(Record):
    blocks: list[ParsedBlock] = Field(min_length=1, max_length=10000)
    parser_version: Text
    input_hash: Hash


class FetchedDocument(Record):
    content_ref: ContentRef
    parsed_content_ref: ContentRef
    final_url: CandidateURL
    media_type: Literal["text/html", "text/plain", "application/pdf"]
    hash: Hash
    locations: list[Location] = Field(min_length=1, max_length=10000)
    parser_version: Text

    @model_validator(mode="after")
    def original_identity(self):
        if self.content_ref.sha256 != self.hash or self.content_ref.media_type != self.media_type:
            raise ValueError("Fetched original reference/hash/media differs")
        if self.parsed_content_ref.media_type != "application/json":
            raise ValueError("Parsed document must be canonical JSON")
        return self
