"""Scout's original-text boundary, not a search-snippet Evidence adapter.

The application must supply an authorized candidate and content verified by the
Fetch adapter. This module checks the immutable parser handoff and quote range;
it does not replace source authorization, SSRF protection, or object-store reads.
"""

import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from domain.documents import FetchedDocument, ParsedDocument
from domain.research.facts import Evidence, Provenance, SourceRecord
from domain.research.ids import stable_id
from domain.research.search import SearchResult


def validate_original(fetched: FetchedDocument, parsed: ParsedDocument) -> None:
    body = json.dumps(
        parsed.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    if (
        parsed.input_hash != fetched.hash
        or parsed.parser_version != fetched.parser_version
        or [block.location for block in parsed.blocks] != fetched.locations
        or hashlib.sha256(body).hexdigest() != fetched.parsed_content_ref.sha256
        or len(body) != fetched.parsed_content_ref.size
    ):
        raise ValueError("Original/parser handoff failed integrity validation")
    for block in parsed.blocks:
        if fetched.media_type == "application/pdf":
            if block.location.page_start is None:
                raise ValueError("PDF evidence requires a parser-supplied page")
        elif block.location.page_start is not None or block.location.page_end is not None:
            raise ValueError("Non-PDF original cannot supply PDF pages")


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and (parts.scheme, port) not in {("http", 80), ("https", 443)}:
        host += f":{port}"
    # Preserve query order/values and path case: either can be semantically significant.
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))


def _source_id(url: str, content_hash: str) -> str:
    parts = urlsplit(url)
    if parts.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        match = re.fullmatch(
            r"/(?:pdf|abs)/(\d{4}\.\d{4,5}|[a-z-]+/\d{7})(v\d+)(?:\.pdf)?", parts.path
        )
        if match:
            return stable_id("src", "arxiv", match[1], match[2])
    # Unversioned URLs and unverified DOI metadata cannot identify a frozen paper.
    return stable_id("src", url, content_hash)


def register_original(
    candidate: SearchResult,
    fetched: FetchedDocument,
    parsed: ParsedDocument,
    *,
    retrieved_at: datetime,
) -> SourceRecord:
    validate_original(fetched, parsed)
    if candidate.source_type == "paper" and fetched.media_type != "application/pdf":
        raise ValueError("Paper original must be a PDF, not an abstract page")
    canonical_url = _canonical_url(fetched.final_url)
    source_id = _source_id(canonical_url, fetched.hash)
    arxiv = urlsplit(canonical_url).hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
    return SourceRecord(
        source_id=source_id,
        source_type=candidate.source_type,
        title=candidate.title,
        authors_or_publisher=candidate.authors_or_publisher,
        published_at=candidate.published_at or None,
        version=candidate.version or None,
        canonical_url=canonical_url,
        provenance=[
            Provenance(
                retrieved_at=retrieved_at,
                retrieved_via="papers" if candidate.source_type == "paper" else "web",
                original_ref=fetched.final_url,
                document_version_id=None,
                upstream_source_id=None,
            )
        ],
        source_tier="primary" if arxiv else candidate.source_tier,
        content_object_key=fetched.content_ref.key,
        content_hash=fetched.hash,
        data_classification="public",
    )


def evidence_from_original(
    source: SourceRecord,
    fetched: FetchedDocument,
    parsed: ParsedDocument,
    *,
    block_index: int,
    quote: str,
    evidence_type: str,
) -> Evidence:
    validate_original(fetched, parsed)
    if (
        source.content_hash != fetched.hash
        or source.content_object_key != fetched.content_ref.key
        or source.source_id != _source_id(_canonical_url(fetched.final_url), fetched.hash)
    ):
        raise ValueError("Source does not identify this original")
    if type(block_index) is not int or not 0 <= block_index < len(parsed.blocks):
        raise ValueError("Evidence block index is outside original")
    block = parsed.blocks[block_index]
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("Evidence quote must be nonempty original text")
    if evidence_type == "result_table" and block.type != "table":
        raise ValueError("Result table evidence requires a parsed table")
    if block.type in {"table", "formula"}:
        # Atomic blocks cannot discard captions/headers/units/footnotes.
        original = "\n".join(
            [block.content, *([block.caption] if block.caption else []), *block.notes]
        )
        if quote != original:
            raise ValueError("Atomic evidence must retain the entire block, caption and notes")
    elif quote not in block.content:
        raise ValueError("Evidence quote is not an exact original-text range")
    normalized = " ".join(quote.split())
    return Evidence(
        evidence_id=stable_id(
            "ev", source.source_id, block.location.model_dump(mode="json"), normalized
        ),
        source_id=source.source_id,
        evidence_type=evidence_type,
        location=block.location,
        quote_or_raw_content=quote,
        extraction_method=f"parser:{parsed.parser_version}:verified_range",
        content_hash=fetched.hash,
    )
