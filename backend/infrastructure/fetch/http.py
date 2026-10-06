"""Restricted original-byte download; parsing/storage is a separate next step.

No Search snippet, invented body, page number, or Evidence is returned here.
DNS is checked at the actual socket connection, which is pinned to an approved
numeric address. TLS still authenticates the original hostname. A fresh pool
per hop forces a new DNS check on every redirect, including same-origin hops.
"""

from __future__ import annotations

import asyncio
import ipaddress
import math
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpcore

from domain.ports import AdapterError

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
Resolver = Callable[[str, int], Awaitable[list[str]]]


def failure(code: str, retryable: bool = False) -> AdapterError:
    return AdapterError("fetch", code, "Original content download failed", retryable, "fetch")


def external_url(url: str) -> str:
    try:
        if not isinstance(url, str) or len(url) > 4096 or not url:
            raise ValueError("Invalid URL")
        if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in url):
            raise ValueError("URL contains control characters")
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or port == 0
            or parsed.username is not None
            or parsed.password is not None
            or "%" in parsed.netloc
        ):
            raise ValueError("Invalid external URL")
        hostname = host.lower().rstrip(".")
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            raise ValueError("Local hostname")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            approved_ip(str(literal))
        # A fragment is not sent over HTTP and must not create a new body identity.
        ascii_host = host.encode("idna").decode("ascii")
        authority = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
        if port is not None:
            authority += f":{port}"
        return urlunsplit(
            (
                parsed.scheme,
                authority,
                quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"),
                quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~"),
                "",
            )
        )
    except (ValueError, TypeError) as exc:
        raise failure("fetch_url_forbidden") from exc


def approved_ip(value: str) -> str:
    try:
        if "%" in value:
            raise ValueError("Scoped IPv6")
        address = ipaddress.ip_address(value)
        effective = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        effective = effective or address
        if (
            not effective.is_global
            or effective.is_multicast
            or effective.is_reserved
            or effective.is_unspecified
            or effective.is_loopback
            or effective.is_link_local
            or isinstance(address, ipaddress.IPv6Address)
            and (address.sixtofour or address.teredo)
        ):
            raise ValueError("Non-public address")
        return str(address)
    except (ValueError, TypeError) as exc:
        raise failure("fetch_url_forbidden") from exc


async def resolve(host: str, port: int) -> list[str]:
    try:
        # Numeric addresses do not need DNS; unusual numeric host spellings still
        # pass getaddrinfo and are checked as concrete IPs before connecting.
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise failure("fetch_unavailable", True) from exc
    return list(dict.fromkeys(info[4][0] for info in infos))


class PublicNetwork(httpcore.AsyncNetworkBackend):
    def __init__(self, *, resolver: Resolver = resolve, backend=None):
        self._resolve = resolver
        self._backend = backend if backend is not None else httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = await self._resolve(host, port)
        if not addresses:
            raise failure("fetch_unavailable", True)
        # Reject mixed public/private answers rather than falling back to a
        # later private address after a public-address connection failure.
        public = [approved_ip(address) for address in addresses]
        return await self._backend.connect_tcp(
            public[0],
            port,
            timeout=timeout,
            local_address=None,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, *args, **kwargs):
        raise failure("fetch_url_forbidden")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class DownloadedBody:
    """Raw, untrusted bytes, not a parsed FetchedDocument or usable Evidence."""

    body: bytes
    final_url: str
    media_type: str
    sha256: str
    etag: str | None
    last_modified: str | None
    redirects: tuple[str, ...]


class RestrictedDownloader:
    def __init__(
        self,
        *,
        timeout_s: float = 30,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        network: PublicNetwork | None = None,
    ):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Download timeout must be finite and positive")
        if type(max_bytes) is not int or not 0 < max_bytes <= MAX_DOWNLOAD_BYTES:
            raise ValueError("Download byte limit must be within 50MiB")
        self._timeout_s, self._max_bytes = timeout_s, max_bytes
        self._network = network if network is not None else PublicNetwork()

    async def download(self, url: str) -> DownloadedBody:
        url = external_url(url)
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._download(url)
        except (TimeoutError, httpcore.TimeoutException) as exc:
            raise failure("fetch_timeout", True) from exc
        except (httpcore.NetworkError, httpcore.ProtocolError) as exc:
            raise failure("fetch_unavailable", True) from exc

    async def _download(self, url):
        visited = []
        for hop in range(4):
            url = external_url(url)
            if url in visited:
                raise failure("fetch_redirect_limit")
            visited.append(url)
            # No proxy/environment credentials/cookies, retries or pool reuse.
            async with (
                httpcore.AsyncConnectionPool(
                    network_backend=self._network,
                    max_connections=1,
                    max_keepalive_connections=0,
                    retries=0,
                    http2=False,
                ) as pool,
                pool.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": "text/html,text/plain,application/pdf",
                        "Accept-Encoding": "identity",
                        "User-Agent": "DR4A/mono-v1",
                    },
                    extensions={
                        "timeout": {
                            key: self._timeout_s for key in ("connect", "read", "write", "pool")
                        }
                    },
                ) as response,
            ):
                headers = {k.lower(): v for k, v in response.headers}
                if response.status in {301, 302, 303, 307, 308}:
                    location = headers.get(b"location")
                    if not location or hop == 3:
                        raise failure("fetch_redirect_limit")
                    try:
                        url = urljoin(url, location.decode("ascii"))
                    except (ValueError, UnicodeError) as exc:
                        raise failure("fetch_url_forbidden") from exc
                    # Close redirect response and pool before next hop.
                    continue
                if response.status != 200:
                    raise failure(
                        "fetch_http_error", response.status == 429 or response.status >= 500
                    )
                if headers.get(b"content-encoding", b"identity").lower() != b"identity":
                    # No uncontrolled decompression of an untrusted response.
                    raise failure("fetch_encoding_unsupported")
                try:
                    media_type = (
                        headers.get(b"content-type", b"")
                        .decode("ascii")
                        .split(";", 1)[0]
                        .lower()
                        .strip()
                    )
                except UnicodeError as exc:
                    raise failure("fetch_media_unsupported") from exc
                if media_type not in {"text/html", "text/plain", "application/pdf"}:
                    raise failure("fetch_media_unsupported")
                if b"content-length" in headers:
                    try:
                        length = int(headers[b"content-length"])
                    except ValueError as exc:
                        raise failure("fetch_response_invalid") from exc
                    if length < 0:
                        raise failure("fetch_response_invalid")
                    if length > self._max_bytes:
                        raise failure("fetch_too_large")
                body = bytearray()
                async for chunk in response.aiter_stream():
                    if len(body) + len(chunk) > self._max_bytes:
                        raise failure("fetch_too_large")
                    body.extend(chunk)
                if not body:
                    raise failure("fetch_empty")
                if media_type == "application/pdf" and not body.startswith(b"%PDF-"):
                    raise failure("fetch_response_invalid")
                if media_type != "application/pdf" and body.startswith(b"%PDF-"):
                    raise failure("fetch_response_invalid")

                def metadata(name, headers=headers):
                    value = headers.get(name)
                    if value is None:
                        return None
                    if len(value) > 4096:
                        raise failure("fetch_response_invalid")
                    return value.decode("latin-1")

                return DownloadedBody(
                    body=bytes(body),
                    final_url=url,
                    media_type=media_type,
                    sha256=sha256(body).hexdigest(),
                    etag=metadata(b"etag"),
                    last_modified=metadata(b"last-modified"),
                    redirects=tuple(visited[:-1]),
                )
        raise AssertionError("Unreachable redirect state")
