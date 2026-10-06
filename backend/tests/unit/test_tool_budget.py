import pytest
from pydantic import ValidationError

from application.errors import AppError
from application.settings import Settings
from application.tool_budget import ToolBudgetRequest, check_tool_budget
from domain.research.state import BudgetUsage


def usage(**changes):
    return BudgetUsage.model_validate(
        {
            "llm_calls": 0,
            "search_calls": 0,
            "fetch_calls": 0,
            "tokens": 0,
            "elapsed_s": 0,
            **changes,
        }
    )


def request(tool="llm", tokens=1000, terminal=False):
    return ToolBudgetRequest(tool=tool, token_reservation=tokens, terminal=terminal)


def check(used, pending, value):
    return check_tool_budget(Settings().run_config_snapshot(), used, pending, value)


def test_unsettled_reservations_count_against_both_model_ceilings():
    result = check(usage(llm_calls=50, tokens=105000), usage(llm_calls=1, tokens=1000), request())
    assert result.llm_calls == 52 and result.tokens == 107000
    with pytest.raises(AppError, match="budget_exhausted"):
        check(usage(llm_calls=51), usage(llm_calls=1), request())
    with pytest.raises(AppError, match="budget_exhausted"):
        check(usage(tokens=107000), usage(tokens=1000), request())


def test_terminal_reserve_is_available_only_when_explicitly_requested():
    with pytest.raises(AppError, match="budget_exhausted"):
        check(usage(llm_calls=52, tokens=108000), usage(), request())
    result = check(usage(llm_calls=59, tokens=119000), usage(), request(terminal=True))
    assert result.llm_calls == 60 and result.tokens == 120000
    with pytest.raises(AppError, match="budget_exhausted"):
        check(result, usage(), request(terminal=True))


@pytest.mark.parametrize(
    "tool,field,limit", [("search", "search_calls", 60), ("fetch", "fetch_calls", 30)]
)
def test_readonly_tool_ceilings_include_pending(tool, field, limit):
    assert (
        getattr(check(usage(**{field: limit - 2}), usage(**{field: 1}), request(tool, 0)), field)
        == limit
    )
    with pytest.raises(AppError, match="budget_exhausted"):
        check(usage(**{field: limit - 1}), usage(**{field: 1}), request(tool, 0))


@pytest.mark.parametrize(
    "tool,tokens,terminal",
    [
        ("llm", 0, False),
        ("llm", True, False),
        ("search", 1, False),
        ("fetch", 0, True),
        ("arbitrary", 0, False),
        ("llm", 1, "true"),
    ],
)
def test_invalid_budget_requests_are_not_coerced(tool, tokens, terminal):
    with pytest.raises(ValidationError):
        request(tool, tokens, terminal)


def test_every_tool_stops_after_deadline_and_corrupt_ledger():
    for tool, tokens in [("llm", 1), ("search", 0), ("fetch", 0), ("analysis", 0)]:
        with pytest.raises(AppError, match="budget_exhausted"):
            check(usage(elapsed_s=1800), usage(), request(tool, tokens))
        with pytest.raises(AppError, match="budget_exhausted"):
            check(usage(tokens=120001), usage(), request(tool, tokens))


def test_calculation_does_not_mutate_or_claim_to_persist_consumption():
    used, pending = usage(llm_calls=1), usage(llm_calls=1)
    before = used.model_dump(), pending.model_dump()
    assert check(used, pending, request()).llm_calls == 3
    assert (used.model_dump(), pending.model_dump()) == before
    with pytest.raises(ValueError):
        check(used, usage(elapsed_s=1), request())
