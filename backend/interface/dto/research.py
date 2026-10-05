"""Research DTOs (request/response models)."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool

from application.research_inputs import (
    ConfirmResearchInput,
    ResearchMessageInput,
    StartResearchInput,
)
from domain.research.models import (
    BriefField,
    Nonnegative,
    PartialResearchBrief,
    Positive,
    Record,
    ResearchBrief,
    SourceSelection,
    Text,
)
from interface.dto.base import RequestDTO


class ResearchRequest(StartResearchInput):
    """POST /research body."""


class MessageRequest(ResearchMessageInput):
    """POST /research/{id}/messages body."""


class ConfirmRequest(ConfirmResearchInput):
    """Explicit acceptance or rejection of the current version."""


class EmptyRequest(RequestDTO):
    """An explicit empty body rejects unexpected control fields."""


class ClarifyBase(Record):
    session_id: UUID
    brief_version: Positive
    clarification_round: Nonnegative
    clarification_limit_reached: StrictBool
    source_selection: SourceSelection


class AskResponse(ClarifyBase):
    status: Literal["ask"]
    questions: Annotated[list[Text], Field(min_length=1, max_length=2)]
    missing_fields: list[BriefField]
    brief_draft: PartialResearchBrief


class ConfirmResponse(ClarifyBase):
    status: Literal["confirm"]
    research_brief: ResearchBrief


class ReadyResponse(Record):
    session_id: UUID
    run_id: UUID
    status: Literal["ready"]
    brief_version: Positive
    sse_url: Text


ClarifyResponse = Annotated[AskResponse | ConfirmResponse, Field(discriminator="status")]
