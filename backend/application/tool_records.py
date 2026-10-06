"""Internal reservation receipts; workers never obtain repository capabilities."""

from typing import Literal

from pydantic import StrictBool, model_validator

from domain.content import ContentRef
from domain.research.models import Positive, Record
from domain.research.state import BudgetUsage
from domain.research.tool_calls import ToolCallRecord


class ToolBudgetView(Record):
    used: BudgetUsage
    pending: BudgetUsage


class ToolReservation(Record):
    disposition: Literal["execute", "cache", "uncertain", "finished"]
    record: ToolCallRecord
    attempt: Positive
    lease_token: Positive
    uncertain_replay: StrictBool
    reference: ContentRef | None
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
        return self
