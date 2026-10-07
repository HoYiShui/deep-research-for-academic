"""T025 contracts: actual adapter code with controlled HTTP, not live dependency proof."""

import asyncio
import json
import time

import httpx
import pytest
from pydantic import ValidationError

from domain.ports import AdapterError, SearchResult
from domain.research.search import SearchBatch, SearchOutcome
from infrastructure.search.arxiv import ArxivSearch, _query_params
from infrastructure.search.arxiv import _parse as parse_arxiv
from infrastructure.search.bocha import BochaSearch
from infrastructure.search.bocha import _parse as parse_bocha
from infrastructure.search.composite import CompositeSearch
from infrastructure.search.http import MAX_RESPONSE_BYTES, RequestSpacing

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title>
<summary>An abstract, not original-text evidence.</summary>
<published>2017-06-12T17:57:34Z</published><author><name>Ashish Vaswani</name></author>
</entry></feed>"""


def candidate(name="a"):
    return SearchResult(
        source_id=name, source_type="web", title=name, snippet="", url=f"https://example.com/{name}"
    )


def bocha_payload(items=None):
    return {"code": 200, "data": {"webPages": {"value": [] if items is None else items}}}


class Source:
    def __init__(self, items=None, failure=None):
        self.items = [] if items is None else items
        self.failure = failure
        self.calls = 0

    async def search(self, query):
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.items


@pytest.mark.parametrize(
    "patch",
    [
        {"url": "file:///etc/passwd"},
        {"url": "https://key@example.com/a"},
        {"url": "https://example.com/a\nb"},
        {"url": "https://example.com:bad/a"},
        {"url": ""},
        {"title": ""},
        {"source_type": "kb"},
        {"snippet": 7},
        {"peer_reviewed": True},
        {"source_tier": "trusted"},
    ],
)
def test_strict_candidate(patch):
    with pytest.raises(ValidationError):
        SearchResult.model_validate(candidate().model_dump() | patch)


def test_candidate_is_not_evidence_and_does_not_guess_tier():
    result = candidate()
    assert result.source_tier == "unknown"
    assert "quote_or_raw_content" not in result.model_dump()


def test_arxiv_metadata_preprint_and_version():
    result = parse_arxiv(ATOM)[0]
    assert result.source_tier == "primary"  # author preprint, not peer-review proof
    assert result.provider == "arxiv"
    assert result.version == "v7"
    assert result.authors_or_publisher == ["Ashish Vaswani"]
    assert result.fulltext_url == "https://arxiv.org/pdf/1706.03762v7"


@pytest.mark.parametrize(
    "body",
    [
        "<html/>",
        "bad XML",
        ATOM.replace("<title>Attention Is All You Need</title>", "<title/>"),
        ATOM.replace("http://arxiv.org/abs/1706.03762v7", "http://arxiv.org/api/errors#bad"),
        '<!DOCTYPE feed [<!ENTITY secret "hidden">]><feed xmlns="http://www.w3.org/2005/Atom"/>',
        b"\xff\xfe<\x00f\x00e\x00e\x00d\x00/\x00>\x00",
    ],
)
def test_arxiv_invalid_and_error_feed_are_not_empty(body):
    with pytest.raises(AdapterError) as caught:
        parse_arxiv(body)
    assert caught.value.code == "search_response_invalid"
    assert not caught.value.retryable


def test_valid_empty_feeds():
    assert parse_arxiv('<feed xmlns="http://www.w3.org/2005/Atom"/>') == []
    assert parse_bocha(json.dumps(bocha_payload()).encode()) == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"code": "200"},
        {"code": True},
        {"code": 200, "data": None},
        {"code": 200, "data": {}},
        bocha_payload([{"url": "https://example.com"}]),
        bocha_payload([{"url": "https://example.com", "name": "t", "snippet": 5}]),
    ],
)
def test_bocha_malformed_is_not_empty(payload):
    with pytest.raises(AdapterError) as caught:
        parse_bocha(json.dumps(payload).encode())
    assert caught.value.code == "search_response_invalid"


@pytest.mark.parametrize("code,retryable", [(401, False), (429, True), (500, True)])
def test_bocha_provider_error(code, retryable):
    with pytest.raises(AdapterError) as caught:
        parse_bocha(json.dumps({"code": code, "msg": "SECRET", "data": None}).encode())
    assert caught.value.code == "search_provider_error"
    assert caught.value.retryable == retryable
    assert "SECRET" not in str(caught.value)


@pytest.mark.asyncio
async def test_bocha_exact_request_metadata_and_close_ownership():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=bocha_payload(
                [
                    {
                        "name": "t",
                        "url": "https://example.com/a",
                        "snippet": "candidate",
                        "siteName": "Publisher",
                        "datePublished": "2026-10-06",
                    }
                ]
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = BochaSearch("test-secret", client=client)
    result = (await adapter.search(" public query "))[0]
    assert len(calls) == 1
    assert calls[0].headers["authorization"] == "Bearer test-secret"
    assert json.loads(calls[0].content) == {
        "query": "public query",
        "count": 10,
        "freshness": "noLimit",
        "summary": False,
    }
    assert result.authors_or_publisher == ["Publisher"]
    assert result.source_tier == "unknown"
    await adapter.aclose()
    assert not client.is_closed  # caller owns injected client
    await client.aclose()


@pytest.mark.asyncio
async def test_arxiv_exact_request_single_attempt():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=ATOM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ArxivSearch(client=client, spacing=RequestSpacing(0))
        assert len(await adapter.search("transformer")) == 1
    assert len(calls) == 1
    assert dict(calls[0].url.params) == {"search_query": "all:transformer", "max_results": "10"}


@pytest.mark.parametrize(
    "query,identifier",
    [
        ("arXiv:1706.03762v7 Attention Is All You Need PDF", "1706.03762v7"),
        ("如何校验 arXiv:1706.03762v7 Table 2 hash？", "1706.03762v7"),
        ("arxiv 1706.03762v7 hash", "1706.03762v7"),
        ("https://arxiv.org/abs/1706.03762v7", "1706.03762v7"),
        ("1706.03762v7", "1706.03762v7"),
        ("arXiv:hep-th/9901001v2", "hep-th/9901001v2"),
    ],
)
async def test_arxiv_explicit_identifier_is_lookup_not_unrelated_prose_search(query, identifier):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=ATOM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await ArxivSearch(client=client, spacing=RequestSpacing(0)).search(query)
    assert len(calls) == 1
    assert dict(calls[0].url.params) == {"id_list": identifier, "max_results": "10"}


@pytest.mark.parametrize(
    "query",
    [
        "Transformer 2017 Table 2",
        "arxiv:1713.03762",
        "arxiv:1706.03762v7extra",
        "notarxiv:1706.03762",
    ],
)
def test_arxiv_does_not_guess_identifiers_from_dates_or_partial_tokens(query):
    assert _query_params(query) == {"search_query": f"all:{query}", "max_results": 10}


def test_arxiv_identifier_lookup_deduplicates_and_refuses_unbounded_ids():
    assert _query_params("arxiv:1706.03762v7 arxiv:1706.03762v7 arxiv:2207.03987v3")["id_list"] == (
        "1706.03762v7,2207.03987v3"
    )
    with pytest.raises(AdapterError) as failure:
        _query_params(" ".join(f"arxiv:1706.{index:05d}" for index in range(11)))
    assert failure.value.code == "search_query_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,retryable,code",
    [
        (302, False, "search_http_error"),
        (401, False, "search_http_error"),
        (429, True, "search_rate_limited"),
        (503, True, "search_http_error"),
    ],
)
async def test_http_errors_have_no_hidden_retries_or_redirects(status, retryable, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="SECRET", headers={"Location": "http://127.0.0.1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AdapterError) as caught:
            await BochaSearch("SECRET", client=client).search("SECRET")
    assert len(calls) == 1
    assert caught.value.code == code
    assert caught.value.retryable == retryable
    assert "SECRET" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx.ConnectError("SECRET"), "search_unavailable"),
        (httpx.ReadTimeout("SECRET"), "search_timeout"),
    ],
)
async def test_network_errors_are_redacted(exception, code):
    def handler(request):
        raise exception

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AdapterError) as caught:
            await BochaSearch("SECRET", client=client).search("SECRET")
    assert caught.value.code == code
    assert "SECRET" not in str(caught.value)


@pytest.mark.asyncio
async def test_oversized_body_rejected():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        )
    ) as client:
        with pytest.raises(AdapterError, match="search_response_too_large"):
            await BochaSearch("key", client=client).search("q")


@pytest.mark.asyncio
async def test_missing_key_no_http(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as client:
        with pytest.raises(AdapterError, match="search_not_configured"):
            await BochaSearch(client=client).search("q")
    assert not calls


@pytest.mark.asyncio
async def test_partial_empty_failed_and_all_failed_distinct():
    bad = Source(failure=RuntimeError("SECRET"))
    good = Source([candidate()])
    composite = CompositeSearch([("good", good), ("bad", bad), ("empty", Source())])
    batch = await composite.search_batch("q")
    assert [item.status for item in batch.outcomes] == ["ok", "failed", "empty"]
    assert len(batch.items) == 1 and not batch.all_failed
    assert composite.take_gaps() == []  # new path does not touch mutable side channel
    assert "SECRET" not in batch.model_dump_json()
    failed = CompositeSearch([("bad", bad)])
    assert (await failed.search_batch("q")).all_failed
    with pytest.raises(AdapterError, match="all_search_sources_failed"):
        await failed.search("q")
    assert await CompositeSearch([("empty", Source())]).search("q") == []


@pytest.mark.asyncio
async def test_timeout_then_nonretryable_error_both_recorded():
    class Changing:
        calls = 0

        async def search(self, query):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError()
            raise RuntimeError("SECRET")

    source = Changing()
    batch = await CompositeSearch([("changing", source)]).search_batch("q")
    outcome = batch.outcomes[0]
    assert outcome.attempts == 2
    assert outcome.failure.code == "search_unavailable"


@pytest.mark.asyncio
async def test_each_physical_attempt_passes_invoker():
    source = Source(failure=TimeoutError())
    attempts = []

    async def invoke(name, query, attempt, operation):
        attempts.append((name, query, attempt))
        return await operation()

    batch = await CompositeSearch([("slow", source)]).search_batch("q", invoke=invoke)
    assert batch.outcomes[0].attempts == 2
    assert attempts == [("slow", "q", 1), ("slow", "q", 2)]
    assert source.calls == 2


@pytest.mark.asyncio
async def test_invoker_control_error_not_source_degradation():
    class LeaseLost(Exception):
        pass

    source = Source([candidate()])

    async def invoke(*args):
        raise LeaseLost("stop")

    with pytest.raises(LeaseLost):
        await CompositeSearch([("good", source)]).search_batch("q", invoke=invoke)
    assert source.calls == 0


@pytest.mark.asyncio
async def test_authorized_categories_do_not_call_other_source():
    paper, web = Source(), Source()
    composite = CompositeSearch([("arxiv", paper), ("bocha", web)])
    batch = await composite.search_batch("q", categories=frozenset({"papers"}))
    assert len(batch.outcomes) == 1 and paper.calls == 1 and web.calls == 0
    with pytest.raises(ValueError, match="knowledge_base"):
        await composite.search_batch("q", categories=frozenset({"knowledge_base"}))


@pytest.mark.asyncio
async def test_parallel_queries_shared_limit_stable_order_and_no_gap_leak():
    active, peak = 0, 0
    entered = asyncio.Event()
    release = asyncio.Event()

    class Blocking:
        def __init__(self, name):
            self.name = name

        async def search(self, query):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
                return [candidate(f"{self.name}-{query}")]
            finally:
                active -= 1

    composite = CompositeSearch(
        [("a", Blocking("a")), ("b", Blocking("b"))],
        semaphore=asyncio.Semaphore(2),
    )
    jobs = [asyncio.create_task(composite.search_batch(query)) for query in ("q1", "q2")]
    await asyncio.wait_for(entered.wait(), 1)
    assert peak == 2  # proves overlap, not just elapsed-time inference
    release.set()
    batches = await asyncio.gather(*jobs)
    assert [[x.source_id for x in b.items] for b in batches] == [["a-q1", "b-q1"], ["a-q2", "b-q2"]]
    assert peak == 2 and active == 0
    assert not composite.take_gaps()


@pytest.mark.asyncio
async def test_timeout_cancels_operation_and_parent_cancel_not_swallowed():
    exited = asyncio.Event()

    class Hanging:
        async def search(self, query):
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

    composite = CompositeSearch([("slow", Hanging())], timeout_s=0.01)
    batch = await composite.search_batch("q", retry=False)
    assert batch.outcomes[0].failure.code == "search_timeout" and exited.is_set()
    exited.clear()
    job = asyncio.create_task(composite.search_batch("q"))
    await asyncio.sleep(0)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job


@pytest.mark.asyncio
async def test_provider_spacing_across_requests():
    calls = []

    def handler(request):
        calls.append(time.monotonic())
        return httpx.Response(200, text=ATOM)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ArxivSearch(client=client, spacing=RequestSpacing(0.025))
        await asyncio.gather(adapter.search("q1"), adapter.search("q2"))
    assert calls[1] - calls[0] >= 0.02


def test_outcome_consistency_and_registry_validation():
    with pytest.raises(ValidationError):
        SearchOutcome(source="a", attempts=1, status="ok", items=[])
    with pytest.raises(ValidationError):
        SearchBatch(outcomes=[SearchOutcome(source="a", attempts=1, status="empty")] * 2)
    with pytest.raises(ValueError):
        CompositeSearch([])
    with pytest.raises(ValueError):
        CompositeSearch([("a", Source()), ("a", Source())])
