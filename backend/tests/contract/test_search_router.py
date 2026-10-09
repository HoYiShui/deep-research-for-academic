"""Gateway adaptation, safe diagnostics and logical-call retry boundaries."""

import json

import httpx
import pytest

from application.settings import Settings
from application.web_search import web_search_binding
from cli.commands.doctor import _check_config
from domain.ports import AdapterError
from domain.research.diagnostics import diagnostic_scope
from infrastructure.search.composite import CompositeSearch
from infrastructure.search.search_router import SearchRouterSearch


def payload():
    return {
        "results": [
            {
                "title": "Attention Is All You Need",
                "url": "https://arxiv.org/abs/1706.03762",
                "content": "A search excerpt, not verified evidence",
                "contentType": "body",
                "score": 0.8,
            }
        ],
        "meta": {
            "provider": "tavily",
            "keyId": "private-key-label",
            "tookMs": 123,
            "degraded": False,
            "attempts": [
                {
                    "provider": "tavily",
                    "keyId": "private-key-label",
                    "ok": True,
                    "code": "",
                    "tookMs": 123,
                }
            ],
        },
    }


async def test_exact_request_actual_provider_and_safe_route_trace():
    def handler(request):
        assert request.url == "http://localhost:8080/search"
        assert json.loads(request.content) == {"query": "attention convolution", "content": "body"}
        assert "authorization" not in request.headers
        return httpx.Response(200, json=payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SearchRouterSearch("http://localhost:8080", client=client)
    trace = []
    try:
        with diagnostic_scope(sink=lambda value, **kw: trace.append(value)):
            results = await adapter.search("attention convolution")
        assert results[0].provider == "tavily" and results[0].source_tier == "unknown"
        assert results[0].snippet == payload()["results"][0]["content"]
        assert "quote_or_raw_content" not in results[0].model_dump()
        assert trace[0]["provider"] == "tavily"
        assert "private-key-label" not in json.dumps(trace)
        await adapter.aclose()
        assert not client.is_closed
    finally:
        await client.aclose()


@pytest.mark.parametrize("patch", ["meta", "url", "capability", "attempt", "score"])
async def test_invalid_gateway_output_is_not_empty(patch):
    body = payload()
    if patch == "meta":
        del body["meta"]
    elif patch == "url":
        body["results"][0]["url"] = "file:///private"
    elif patch == "capability":
        body["results"][0]["contentType"] = "abstract"
    elif patch == "attempt":
        body["meta"]["attempts"] = []
    else:
        body["results"][0]["score"] = "not-a-score"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    ) as client:
        adapter = SearchRouterSearch(client=client)
        with pytest.raises(AdapterError) as error:
            await adapter.search("query")
        assert error.value.code == "search_response_invalid"


async def test_gateway_failure_is_not_retried_by_composite_or_leaked():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(502, text="SECRET upstream body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = SearchRouterSearch(client=client)
        composite = CompositeSearch(
            [("search_router", source)], source_categories={"search_router": "web"}
        )
        batch = await composite.search_batch("query")
        assert batch.all_failed and batch.outcomes[0].attempts == 1 and len(requests) == 1
        assert "SECRET" not in batch.model_dump_json()


async def test_valid_empty_is_not_provider_failure():
    body = payload() | {"results": []}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    ) as client:
        assert await SearchRouterSearch(client=client).search("query") == []


def test_default_shared_binding_and_explicit_bocha_fallback():
    settings = Settings()
    binding = web_search_binding(settings)
    assert binding.providers[0].name == "search_router"
    assert binding.providers[0].category == "web"
    assert (
        web_search_binding(settings.model_copy(update={"web_search_provider": "bocha"}))
        .providers[0]
        .name
        == "bocha"
    )


def test_doctor_router_profile_does_not_require_bocha_key():
    settings = Settings(
        database_url="postgresql://localhost/test",
        anthropic_api_key="test",
        minio_access_key="test",
        minio_secret_key="test",
    )
    assert _check_config(settings)
    assert not _check_config(settings.model_copy(update={"web_search_provider": "bocha"}))
