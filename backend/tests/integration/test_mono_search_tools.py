"""Real Driver/PG/MinIO; controlled providers, never a public-paper claim."""

import asyncio

import pytest

from application.errors import AppError
from application.search_tools import SearchBinding, SearchProvider
from application.tool_budget import ToolBudgetRequest
from application.tool_calls import ToolOutput
from domain.ports import AdapterError
from domain.research.search import SearchResult
from infrastructure.search.composite import CompositeSearch
from tests.integration.test_mono_run_driver import world
from tests.integration.test_mono_tool_cache import identity, service


class Provider:
    def __init__(self, kind, *, fail_once=False, empty=False, first_failure=None):
        self.kind, self.fail_once, self.empty = kind, fail_once, empty
        self.first_failure = first_failure
        self.requests = []

    async def search(self, query):
        self.requests.append(query)
        await asyncio.sleep(0.01)
        if self.first_failure is not None and len(self.requests) == 1:
            raise self.first_failure
        if self.fail_once and len(self.requests) == 1:
            raise AdapterError("search", "search_timeout", "Controlled timeout", True, "search")
        if self.empty:
            return []
        return [
            SearchResult(
                source_id="candidate",
                source_type=self.kind,
                title="Controlled candidate",
                snippet="Not original evidence",
                url="https://example.org/source",
            )
        ]


def binding(paper, web):
    return SearchBinding(
        CompositeSearch([("arxiv", paper), ("bocha", web)]),
        (
            SearchProvider(name="arxiv", category="papers", revision="fixture-v1"),
            SearchProvider(name="bocha", category="web", revision="fixture-v1"),
        ),
    )


@pytest.mark.parametrize("fail_once", [False, True])
async def test_each_provider_attempt_is_durable_and_repeated_unit_call_uses_cache(
    pg_database, object_cache, fail_once
):
    paper, web = Provider("paper", fail_once=fail_once), Provider("web", empty=True)
    batches = []

    async def before(value, context, *_):
        if value.phase != "research" or context.unit.parameters["kind"] != "query":
            return
        arguments = {"query": context.unit.parameters["query"]}
        first = await context.invoke("search", arguments)
        requests = len(paper.requests) + len(web.requests)
        second = await context.invoke("search", arguments)
        # Physical retries are not a changed semantic call. Replaying the same
        # unit returns cached candidates without another provider operation.
        assert first["outcomes"][0]["items"] == second["outcomes"][0]["items"]
        assert requests == len(paper.requests) + len(web.requests)
        assert first["outcomes"][1]["status"] == "empty"
        batches.append(first)

    pool, store, user, commit, claimed, _, _, _, _, _, make_driver = await world(
        pg_database, object_cache, before_worker=before
    )
    await make_driver(search=binding(paper, web)).execute(claimed, asyncio.Event())
    assert batches
    expected = len(batches) * 2 + int(fail_once)
    assert len(paper.requests) + len(web.requests) == expected
    assert (
        await pool.fetchval("SELECT count(*) FROM tool_call_attempts WHERE tool='search'")
        == expected
    )
    budget = await store.research.load_tool_budget(user.user_id, commit.run.run_id)
    assert budget.used.search_calls == expected and budget.pending.search_calls == 0
    assert await pool.fetchval("SELECT count(*) FROM tool_calls WHERE status='failed'") == 0
    if fail_once:
        assert batches[0]["outcomes"][0]["attempts"] == 2
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM tool_call_attempts WHERE tool='search' AND status='uncertain'"
            )
            == 1
        )


async def test_workers_cannot_change_query_or_request_search_outside_research(
    pg_database, object_cache
):
    paper, web = Provider("paper"), Provider("web")

    async def before(value, context, *_):
        if value.phase == "plan":
            with pytest.raises(AppError, match="invalid_state"):
                await context.invoke("search", {"query": "arbitrary"})
        if value.phase == "research" and context.unit.parameters["kind"] == "query":
            for arguments in (
                {"query": "changed"},
                {"query": context.unit.parameters["query"], "categories": ["web"]},
            ):
                with pytest.raises(AppError, match="invalid_state"):
                    await context.invoke("search", arguments)

    pool, _, _, _, claimed, _, _, _, _, _, make_driver = await world(
        pg_database, object_cache, before_worker=before
    )
    await make_driver(search=binding(paper, web)).execute(claimed, asyncio.Event())
    assert paper.requests == web.requests == []
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM tool_calls WHERE call_id IN (SELECT call_id FROM tool_call_attempts WHERE tool='search')"
        )
        == 0
    )


async def test_corrupt_successful_search_cache_is_not_a_provider_failure_or_retry(
    pg_database, object_cache
):
    paper, web = Provider("paper"), Provider("web")
    touched = False

    async def before(value, context, store, *_):
        nonlocal touched
        if touched or value.phase != "research" or context.unit.parameters["kind"] != "query":
            return
        touched = True
        arguments = {"query": context.unit.parameters["query"]}
        await context.invoke("search", arguments)
        pool = store.pool
        key = await pool.fetchval(
            "SELECT result_object_key FROM tool_calls WHERE call_id IN (SELECT call_id FROM tool_call_attempts WHERE tool='search') LIMIT 1"
        )
        original_read = object_cache.read

        async def corrupt(reference):
            if reference.key == key:
                raise AdapterError(
                    "minio", "content_invalid", "Controlled corruption", False, "tool_cache"
                )
            return await original_read(reference)

        object_cache.read = corrupt
        try:
            with pytest.raises(AdapterError, match="content_invalid"):
                await context.invoke("search", arguments)
        finally:
            object_cache.read = original_read
        assert len(paper.requests) == len(web.requests) == 1

    _, _, _, _, claimed, _, _, _, _, _, make_driver = await world(
        pg_database, object_cache, before_worker=before
    )
    await make_driver(search=binding(paper, web)).execute(claimed, asyncio.Event())
    assert touched


async def test_search_budget_denial_propagates_before_provider_io(pg_database, object_cache):
    paper, web = Provider("paper"), Provider("web")

    async def before(value, context, *_):
        if value.phase == "research" and context.unit.parameters["kind"] == "query":
            with pytest.raises(AppError, match="budget_exhausted"):
                await context.invoke("search", {"query": context.unit.parameters["query"]})
            # This probe tests the search boundary, not a fixture Writer's
            # contraction report. The default-worker CLI test covers that
            # policy with the actual registered Writer and Critic.
            raise AppError("controlled_probe_complete", "Search budget denial verified")

    pool, store, _, _, claimed, _, _, _, _, _, make_driver = await world(
        pg_database, object_cache, before_worker=before
    )
    limit = claimed.run.config_snapshot.limits.search_calls
    calls = service(store, claimed, object_cache)

    async def empty():
        return ToolOutput(content=[], tokens_used=0)

    for index in range(limit):
        await calls.invoke(
            identity(claimed, index),
            ToolBudgetRequest(tool="search", token_reservation=0, terminal=False),
            empty,
        )
    with pytest.raises(AppError, match="controlled_probe_complete"):
        await make_driver(search=binding(paper, web)).execute(claimed, asyncio.Event())
    assert paper.requests == web.requests == []
    assert (
        await pool.fetchval("SELECT count(*) FROM tool_call_attempts WHERE tool='search'") == limit
    )


async def test_a_new_invocation_cannot_silently_replay_a_previous_uncertain_call(
    pg_database, object_cache
):
    paper = Provider(
        "paper",
        first_failure=AdapterError(
            "search", "search_response_invalid", "Controlled malformed response", False, "search"
        ),
    )
    web, checked = Provider("web"), False

    async def before(value, context, *_):
        nonlocal checked
        if checked or value.phase != "research" or context.unit.parameters["kind"] != "query":
            return
        checked = True
        arguments = {"query": context.unit.parameters["query"]}
        outcome = await context.invoke("search", arguments)
        assert outcome["outcomes"][0]["status"] == "failed"
        assert outcome["outcomes"][0]["failure"]["code"] == "search_response_invalid"
        with pytest.raises(AppError, match="tool_call_uncertain"):
            await context.invoke("search", arguments)
        assert len(paper.requests) == len(web.requests) == 1

    _, _, _, _, claimed, _, _, _, _, _, make_driver = await world(
        pg_database, object_cache, before_worker=before
    )
    await make_driver(search=binding(paper, web)).execute(claimed, asyncio.Event())
    assert checked
