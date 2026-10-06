"""Internal reservation receipts; workers never obtain repository capabilities."""

from typing import Literal

from pydantic import StrictBool, model_validator

from domain.content import ContentRef
from domain.research.models import Nonnegative, Positive, Record
from domain.research.state import BudgetUsage
from domain.research.tool_calls import ToolCallRecord


class ToolBudgetView(Record):
    used: BudgetUsage
    pending: BudgetUsage
    pending_attempts: Nonnegative = 0


class ToolReservation(Record):
    disposition: Literal["execute", "cache", "recover", "uncertain", "finished"]
    record: ToolCallRecord
    attempt: Positive
    lease_token: Positive
    uncertain_replay: StrictBool
    reference: ContentRef | None
    staged_tokens: Nonnegative | None = None
    budget: ToolBudgetView

    @model_validator(mode="after")
    def check_receipt(self):
        if self.disposition == "cache" and (
            self.record.status != "succeeded" or self.reference is None
        ):
            raise ValueError("Cache receipt requires a durable successful result")
        if self.reference is not None and (
            self.reference.key != self.record.result_object_key
            or self.reference.sha256 != self.record.result_hash
        ):
            raise ValueError("Cache reference differs from the persisted call")
        if self.disposition == "execute" and self.record.status != "reserved":
            raise ValueError("Execution receipt requires a reservation")
        if self.disposition == "recover" and (
            self.reference is None
            or self.staged_tokens is None
            or self.record.status not in {"uncertain", "failed"}
        ):
            raise ValueError("Recovery requires a staged result and known usage")
        return self
