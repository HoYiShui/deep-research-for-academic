"""Pure reservation policy, called under the persistent Run budget lock.

`used` is settled consumption, including conservatively charged uncertain
attempts; `pending` contains only outstanding reservations, not settled calls.
This calculation alone does not provide cross-process atomicity. The ledger
adapter must load those values and insert the reservation in one transaction.
"""

from typing import Literal

from pydantic import StrictBool, model_validator

from application.errors import AppError
from domain.research.models import Nonnegative, Record, RunConfig
from domain.research.state import BudgetUsage


class ToolBudgetRequest(Record):
    tool: Literal["llm", "search", "fetch", "analysis"]
    token_reservation: Nonnegative
    terminal: StrictBool

    @model_validator(mode="after")
    def coherent_reservation(self):
        if (self.tool == "llm") != (self.token_reservation > 0):
            raise ValueError("Only model calls require a positive token reservation")
        if self.terminal and self.tool != "llm":
            raise ValueError("Terminal reserve is only for contraction/review model calls")
        return self


def check_tool_budget(
    config: RunConfig,
    used: BudgetUsage,
    pending: BudgetUsage,
    request: ToolBudgetRequest,
) -> BudgetUsage:
    """Return projected charge, not measured provider usage or persisted state.

    The coordinator, not the model, selects terminal mode. Token reservation is
    a bound supplied by the provider adapter/coordinator; it is not a fee or
    a tokenizer estimate represented as measured consumption.
    """
    config = RunConfig.model_validate(config)
    used = BudgetUsage.model_validate(used)
    pending = BudgetUsage.model_validate(pending)
    request = ToolBudgetRequest.model_validate(request)
    if pending.elapsed_s != 0:
        raise ValueError("Pending reservations cannot double-count execution time")
    projected = {
        field: getattr(used, field) + getattr(pending, field) for field in BudgetUsage.model_fields
    }
    if request.tool != "analysis":
        projected[request.tool + "_calls"] += 1
    projected["tokens"] += request.token_reservation
    limits = config.limits
    ceilings = {
        field: getattr(limits, field)
        for field in ("llm_calls", "search_calls", "fetch_calls", "tokens")
    }
    if not request.terminal:
        ceilings["llm_calls"] -= limits.terminal_reserved_calls
        ceilings["tokens"] -= limits.terminal_reserved_tokens
    # Read-only search/fetch do not spend the reserved model budget. Existing
    # terminal spend does not independently forbid them; their own ceilings and
    # the Run deadline still apply. LLM must satisfy both model ceilings.
    checked = (
        {"llm_calls", "tokens"}
        if request.tool == "llm"
        else {request.tool + "_calls"}
        if request.tool in {"search", "fetch"}
        else set()
    )
    if projected["elapsed_s"] >= limits.deadline_s or any(
        projected[field] > ceilings[field] for field in checked
    ):
        raise AppError("budget_exhausted", "Execution budget cannot reserve this tool call")
    # A malformed ledger that is already over the absolute ceiling must stop
    # every tool, even a nominally uncharged local analysis invocation.
    if any(projected[field] > getattr(limits, field) for field in ceilings):
        raise AppError("budget_exhausted", "Persisted execution budget exceeds its limit")
    return BudgetUsage.model_validate(projected)
