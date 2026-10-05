"""Strict application inputs shared by HTTP and debugging callers."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from domain.research.models import (
    PartialResearchBrief,
    Positive,
    Record,
    SourceCategory,
    SourceSelection,
    TaskType,
    Text,
)

Content = Annotated[Text, Field(max_length=16000)]


class StartResearchInput(Record):
    query: Content
    task_type: TaskType | None = None
    sources: list[SourceCategory] = Field(default_factory=lambda: ["papers", "web"])
    knowledge_base_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalized_sources(self):
        selection = SourceSelection(
            categories=self.sources, knowledge_base_ids=self.knowledge_base_ids
        )
        object.__setattr__(self, "sources", selection.categories)
        object.__setattr__(self, "knowledge_base_ids", selection.knowledge_base_ids)
        if "task_type" in self.model_fields_set and self.task_type is None:
            raise ValueError("Optional task type must be omitted, not null")
        return self

    @property
    def selection(self):
        return SourceSelection(categories=self.sources, knowledge_base_ids=self.knowledge_base_ids)


class ResearchMessageInput(Record):
    content: Content
    brief_version: Positive
    brief_patch: PartialResearchBrief | None = None
    source_selection: SourceSelection | None = None

    @model_validator(mode="after")
    def omitted_not_null(self):
        if any(
            name in self.model_fields_set and getattr(self, name) is None
            for name in ("brief_patch", "source_selection")
        ):
            raise ValueError("Optional patch and sources must be omitted, not null")
        return self


class ConfirmResearchInput(Record):
    accepted: StrictBool
    brief_version: Positive
    feedback: Content | None = None
    source_selection: SourceSelection | None = None

    @model_validator(mode="after")
    def confirmation_shape(self):
        if self.accepted:
            if {"feedback", "source_selection"} & self.model_fields_set:
                raise ValueError("Acceptance cannot modify the brief or sources")
        elif self.feedback is None:
            raise ValueError("Rejection requires feedback")
        if "source_selection" in self.model_fields_set and self.source_selection is None:
            raise ValueError("Sources must be omitted, not null")
        return self


class ResearchResponse(Record):
    status_code: Annotated[int, Field(ge=200, lt=400)]
    body: dict
