"""Strict mono wire events. Legacy dataclasses remain isolated for the old CLI."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from domain.research.facts import SectionID
from domain.research.models import (
    UTC,
    Failure,
    Nonnegative,
    Positive,
    Record,
    ResearchPhase,
    ReviewVerdict,
    RunStatus,
    Text,
)

Message = Annotated[Text, Field(max_length=2000)]


class EventBase(Record):
    event_id: UUID
    session_id: UUID
    run_id: UUID
    timestamp: UTC
    checkpoint_seq: Positive


class PhaseFrame(EventBase):
    event: Literal["phase"]
    phase: ResearchPhase
    status: RunStatus
    message: Message


class ProgressFrame(EventBase):
    event: Literal["progress"]
    phase: ResearchPhase
    section_id: SectionID | None
    stage: Literal[
        "query_started",
        "query_completed",
        "source_degraded",
        "section_completed",
        "analysis_completed",
        "draft_completed",
        "review_completed",
    ]
    unit_id: Text
    completed_units: Nonnegative
    total_units: Nonnegative | None
    message: Message
    results: dict | None = None
    chart: dict | None = None

    @model_validator(mode="after")
    def bounded_summary(self):
        if len(self.model_dump_json().encode("utf-8")) > 8192:
            raise ValueError("Progress summary exceeds 8 KiB")
        if self.total_units is not None and self.completed_units > self.total_units:
            raise ValueError("Progress exceeds total units")
        return self


class ReworkFrame(EventBase):
    event: Literal["rework"]
    issue_ids: list[Text]
    action: Literal["re_research", "re_analyze", "revise", "acknowledge_limit"]
    target: Literal["research", "analyze", "write"]
    section_ids: list[SectionID]
    draft_version: Positive
    message: Message


class ErrorFrame(EventBase):
    event: Literal["error"]
    code: Text
    message: Message
    recoverable: StrictBool
    fatal: StrictBool


class DoneFrame(EventBase):
    event: Literal["done"]
    status: Literal["completed", "failed", "cancelled"]
    review_verdict: ReviewVerdict | None
    final_report_url: Text | None
    failure: Failure | None

    @model_validator(mode="after")
    def delivery_identity(self):
        if self.status == "completed":
            if (
                self.review_verdict is None
                or self.final_report_url != f"/research/{self.session_id}/report"
                or self.failure is not None
            ):
                raise ValueError("Completed event requires its committed report")
        elif self.final_report_url is not None:
            raise ValueError("Failed or cancelled event cannot advertise a report")
        if self.status == "failed" and self.failure is None:
            raise ValueError("Failed event requires a persisted Failure")
        return self


RunFrame = Annotated[
    PhaseFrame | ProgressFrame | ReworkFrame | ErrorFrame | DoneFrame, Field(discriminator="event")
]
