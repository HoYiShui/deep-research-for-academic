"""Single physical request helpers: bounded bodies and redacted adapter errors."""

from __future__ import annotations

import asyncio
import math
import time

import httpx

from domain.ports import AdapterError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def error(provider: str, code: str, retryable: bool = False) -> AdapterError:
    return AdapterError(provider, code, "Search dependency request failed", retryable, "search")


def query_text(query: str) -> str:
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise ValueError("Search query must be a nonempty string of at most 4000 characters")
    return query.strip()


class RequestSpacing:
    """Process-local provider limiter; share the adapter at the composition root."""

    def __init__(self, seconds: float):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Invalid search request spacing")
        self.seconds = seconds
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self):
        async with self._lock:
            delay = max(0.0, self._next - time.monotonic())
            if delay:
                await asyncio.sleep(delay)
            self._next = time.monotonic() + self.seconds


class SearchHTTP:
    def __init__(self, provider: str, timeout_s: float, client: httpx.AsyncClient | None):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Search timeout must be finite and positive")
        self.provider = provider
        self.timeout_s = timeout_s
        self._owned = client is None
        self.client = client or httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )

    async def request(self, method: str, url: str, **kwargs) -> bytes:
        try:
            async with asyncio.timeout(self.timeout_s):
                async with self.client.stream(
                    method, url, timeout=self.timeout_s, follow_redirects=False, **kwargs
                ) as response:
                    status = response.status_code
                    if status != 200:
                        code = "search_rate_limited" if status == 429 else "search_http_error"
                        raise error(self.provider, code, status == 429 or status >= 500)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise error(self.provider, "search_response_too_large")
                    return bytes(body)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise error(self.provider, "search_timeout", True) from exc
        except httpx.RequestError as exc:
            raise error(self.provider, "search_unavailable", True) from exc

    async def aclose(self):
        if self._owned:
            await self.client.aclose()
