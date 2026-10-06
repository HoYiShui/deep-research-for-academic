"""Actual HTTP stack with socket-boundary replay, including DNS pin/TLS/redirects."""

import asyncio
import ssl
from hashlib import sha256

import httpcore
import pytest

from domain.ports import AdapterError
from infrastructure.fetch.http import (
    PublicNetwork,
    RestrictedDownloader,
    approved_ip,
    external_url,
    resolve,
)


def response(body=b"original", status=200, media="text/plain", headers=b""):
    return (
        f"HTTP/1.1 {status} Test\r\nContent-Type: {media}\r\nContent-Length: {len(body)}\r\n".encode()
        + headers
        + b"Connection: close\r\n\r\n"
        + body
    )


class Stream(httpcore.AsyncMockStream):
    def __init__(self, body, owner):
        super().__init__([body])
        self.owner = owner

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        assert ssl_context.check_hostname and ssl_context.verify_mode == ssl.CERT_REQUIRED
        self.owner.tls.append(server_hostname)
        return self

    async def write(self, buffer, timeout=None):
        self.owner.writes.append(buffer)
        await super().write(buffer, timeout=timeout)

    async def aclose(self):
        self.owner.closed += 1
        await super().aclose()


class Network:
    def __init__(self, responses):
        self.responses = list(responses)
        self.connected = []
        self.tls, self.writes = [], []
        self.closed = 0

    async def connect_tcp(self, host, port, **kwargs):
        self.connected.append((host, port))
        return Stream(self.responses.pop(0), self)


class DNS:
    def __init__(self, addresses=None):
        self.addresses = ["93.184.216.34"] if addresses is None else addresses
        self.calls = []

    async def __call__(self, host, port):
        self.calls.append((host, port))
        return self.addresses


def downloader(responses, addresses=None, **kwargs):
    net, dns = Network(responses), DNS(addresses)
    adapter = RestrictedDownloader(network=PublicNetwork(resolver=dns, backend=net), **kwargs)
    return adapter, net, dns


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "255.255.255.255",
        "192.0.2.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "2002:7f00:0001::1",
        "fe80::1%en0",
        "garbage",
    ],
)
def test_nonpublic_ip_forbidden(ip):
    with pytest.raises(AdapterError, match="fetch_url_forbidden"):
        approved_ip(ip)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com",
        "http://localhost",
        "http://foo.localhost",
        "http://localhost./",
        "http://service.local/",
        "http://metadata.internal/",
        "https://secret@example.com",
        "https://example.com:bad",
        "http://[::1%25en0]/",
        "http://example.com\\@127.0.0.1/",
        "http://example.com/\nsecret",
        "http://example.com:0/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://198.18.0.91/",
    ],
)
def test_url_syntax_forbidden(url):
    with pytest.raises(AdapterError, match="fetch_url_forbidden"):
        external_url(url)


def test_url_unicode_and_fragment():
    assert (
        external_url("https://example.com/研究?q=证据#ignored")
        == "https://example.com/%E7%A0%94%E7%A9%B6?q=%E8%AF%81%E6%8D%AE"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["2130706433", "127.1", "0x7f000001"])
async def test_unusual_numeric_hosts_resolve_to_rejected_loopback(host):
    addresses = await resolve(host, 80)
    assert addresses
    for address in addresses:
        with pytest.raises(AdapterError, match="fetch_url_forbidden"):
            approved_ip(address)


@pytest.mark.asyncio
async def test_pinned_connection_original_host_and_tls_no_ambient_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    adapter, net, dns = downloader(
        [response(headers=b'ETag: "v1"\r\nLast-Modified: yesterday\r\n')]
    )
    found = await adapter.download("https://example.com/a#fragment")
    assert found.body == b"original" and found.sha256 == sha256(b"original").hexdigest()
    assert found.etag == '"v1"' and found.last_modified == "yesterday"
    assert found.final_url == "https://example.com/a"
    assert dns.calls == [("example.com", 443)]
    assert net.connected == [("93.184.216.34", 443)]  # no second hostname DNS
    assert net.tls == ["example.com"]  # TLS authenticates hostname, not pinned IP
    request = b"".join(net.writes).lower()
    assert b"host: example.com" in request
    assert b"authorization" not in request and b"cookie" not in request
    assert net.closed >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses", [["127.0.0.1"], ["93.184.216.34", "10.0.0.1"], ["::ffff:169.254.169.254"]]
)
async def test_dns_all_addresses_checked_before_any_socket(addresses):
    adapter, net, _ = downloader([response()], addresses)
    with pytest.raises(AdapterError, match="fetch_url_forbidden"):
        await adapter.download("https://example.com")
    assert net.connected == []


@pytest.mark.asyncio
async def test_same_host_redirect_rechecks_dns_rebinding():
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    calls = []

    async def dns(host, port):
        calls.append(host)
        return next(answers)

    net = Network([response(status=302, headers=b"Location: /other\r\n"), response()])
    adapter = RestrictedDownloader(network=PublicNetwork(resolver=dns, backend=net))
    with pytest.raises(AdapterError, match="fetch_url_forbidden"):
        await adapter.download("https://example.com/first")
    assert calls == ["example.com", "example.com"]
    assert len(net.connected) == 1


@pytest.mark.asyncio
async def test_redirect_relative_final_url_hash_and_three_hop_limit():
    adapter, net, dns = downloader(
        [
            response(status=302, headers=b"Location: /next\r\n"),
            response(b"real body"),
        ]
    )
    found = await adapter.download("https://example.com/start")
    assert found.final_url == "https://example.com/next"
    assert found.redirects == ("https://example.com/start",)
    assert found.sha256 == sha256(b"real body").hexdigest()
    assert len(dns.calls) == len(net.connected) == 2
    adapter, net, _ = downloader(
        [response(status=302, headers=f"Location: /{i}\r\n".encode()) for i in range(4)]
    )
    with pytest.raises(AdapterError, match="fetch_redirect_limit"):
        await adapter.download("https://example.com/start")
    assert len(net.connected) == 4  # three redirects, never a fifth request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location", [b"http://localhost/a", b"file:///etc/passwd", b"http://secret@example.com/"]
)
async def test_redirect_forbidden_url_no_second_connect(location):
    adapter, net, _ = downloader([response(status=302, headers=b"Location: " + location + b"\r\n")])
    with pytest.raises(AdapterError, match="fetch_url_forbidden"):
        await adapter.download("https://example.com")
    assert len(net.connected) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,code",
    [
        (response(b"", media="text/plain"), "fetch_empty"),
        (response(b"html disguised as PDF", media="application/pdf"), "fetch_response_invalid"),
        (response(b"%PDF-1.7", media="text/html"), "fetch_response_invalid"),
        (response(media="application/octet-stream"), "fetch_media_unsupported"),
        (response(status=404), "fetch_http_error"),
        (response(headers=b"Content-Encoding: gzip\r\n"), "fetch_encoding_unsupported"),
    ],
)
async def test_invalid_download_not_fake_body(raw, code):
    adapter, net, _ = downloader([raw])
    with pytest.raises(AdapterError) as caught:
        await adapter.download("https://example.com")
    assert caught.value.code == code
    assert "disguised" not in str(caught.value)
    assert net.closed >= 1


@pytest.mark.asyncio
async def test_pdf_raw_download_does_not_claim_parsed_pages():
    adapter, _, _ = downloader(
        [response(b"%PDF-1.7\nraw unvalidated bytes", media="application/pdf")]
    )
    found = await adapter.download("https://example.com/paper.pdf")
    assert found.media_type == "application/pdf"
    assert not hasattr(found, "locations") and not hasattr(found, "text")


@pytest.mark.asyncio
async def test_size_header_and_stream_limit():
    adapter, _, _ = downloader([response(b"12345")], max_bytes=4)
    with pytest.raises(AdapterError, match="fetch_too_large"):
        await adapter.download("https://example.com")
    raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n12345"
    adapter, _, _ = downloader([raw], max_bytes=4)
    with pytest.raises(AdapterError, match="fetch_too_large"):
        await adapter.download("https://example.com")


@pytest.mark.asyncio
async def test_total_deadline_covers_dns_and_cancellation_propagates():
    exited = asyncio.Event()

    async def hanging_dns(host, port):
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    adapter = RestrictedDownloader(timeout_s=0.01, network=PublicNetwork(resolver=hanging_dns))
    with pytest.raises(AdapterError, match="fetch_timeout"):
        await adapter.download("https://example.com")
    assert exited.is_set()
    job = asyncio.create_task(adapter.download("https://example.com"))
    await asyncio.sleep(0)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
