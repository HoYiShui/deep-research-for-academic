"""Search-router candidates; gateway excerpts never become verified Evidence."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError

from domain.research.diagnostics import diagnostic
from domain.research.models import Record, Text
from domain.research.search import SearchResult
from infrastructure.search.http import SearchHTTP, error, query_text


class RouterItem(Record):
    title: Text
    url: Text
    content: StrictStr
    contentType: Literal["abstract", "body"]
    score: StrictFloat | None = Field(default=None, allow_inf_nan=False)
    publishedDate: StrictStr | None = None


class RouterAttempt(Record):
    provider: Text
    keyId: StrictStr | None = None
    ok: StrictBool
    code: StrictStr = ""
    message: StrictStr | None = None
    tookMs: StrictInt = Field(ge=0)


class RouterMeta(Record):
    provider: Text
    keyId: StrictStr
    tookMs: StrictInt = Field(ge=0)
    degraded: StrictBool
    switchedFrom: StrictStr | None = None
    ignoredParams: list[StrictStr] | None = None
    attempts: list[RouterAttempt] = Field(min_length=1, max_length=100)


class RouterResponse(Record):
    results: list[RouterItem] = Field(max_length=50)
    meta: RouterMeta


class SearchRouterSearch:
    handles_retries = True  # Key/provider retries belong to the gateway, not CompositeSearch.

    def __init__(
        self, base_url="http://127.0.0.1:8080", *, content="body", timeout_s=20, client=None
    ):
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Search router URL must be HTTP(S) without credentials")
        if content not in {"any", "body"}:
            raise ValueError("Invalid search router content preference")
        self.url, self.content = base_url.rstrip("/") + "/search", content
        self._http = SearchHTTP("search_router", timeout_s, client)

    async def search(self, query):
        body = await self._http.request(
            "POST", self.url, json={"query": query_text(query), "content": self.content}
        )
        try:
            response = RouterResponse.model_validate_json(body)
            last = response.meta.attempts[-1]
            if not last.ok or last.provider != response.meta.provider:
                raise ValueError("Gateway metadata has no matching successful attempt")
            if self.content == "body" and any(
                item.contentType != "body" for item in response.results
            ):
                raise ValueError("Gateway did not satisfy requested content capability")
            results = [
                SearchResult(
                    source_id=item.url,
                    source_type="web",
                    title=item.title,
                    url=item.url,
                    snippet=item.content[:20000],
                    published_at=item.publishedDate or "",
                    provider=response.meta.provider,
                    source_tier="unknown",
                )
                for item in response.results
            ]
        except (ValueError, TypeError, ValidationError):
            raise error("search_router", "search_response_invalid") from None
        # No key IDs, upstream messages or arbitrary bodies in default diagnostics.
        diagnostic(
            "search_router_route",
            provider=response.meta.provider,
            took_ms=response.meta.tookMs,
            degraded=response.meta.degraded,
            switched_from=response.meta.switchedFrom,
            ignored_params=response.meta.ignoredParams or [],
            attempts=[
                {
                    "provider": item.provider,
                    "ok": item.ok,
                    "code": item.code,
                    "took_ms": item.tookMs,
                }
                for item in response.meta.attempts
            ],
            content_types=[item.contentType for item in response.results],
            scores=[item.score for item in response.results],
        )
        return results

    async def aclose(self):
        await self._http.aclose()
