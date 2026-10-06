"""Original download -> shared content -> versioned parser -> immutable mapping.

Composition root supplies an owned Run and parser config, never model-selected
paths/configuration. Source-policy/candidate authorization and tool budgets are
application responsibilities, and are not inferred from this adapter succeeding.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from application.ports import ContentStorePort, DocumentParserPort
from domain.documents import FetchedDocument, ParsedDocument, ParserConfig
from domain.ports import AdapterError, SearchResult
from infrastructure.fetch.http import RestrictedDownloader


class HTTPDocumentFetch:
    def __init__(
        self,
        content: ContentStorePort,
        parser: DocumentParserPort,
        config: ParserConfig,
        run_id: UUID,
        *,
        downloader: RestrictedDownloader | None = None,
    ):
        if not isinstance(run_id, UUID):
            raise TypeError("Composition root must supply an owned Run UUID")
        self._content, self._parser = content, parser
        self._config = ParserConfig.model_validate_json(config.model_dump_json())
        self._prefix = f"research-content/{run_id}/"
        self._downloader = downloader if downloader is not None else RestrictedDownloader()

    async def fetch(self, candidate: SearchResult) -> FetchedDocument:
        candidate = SearchResult.model_validate_json(candidate.model_dump_json())
        # Paper candidates fetch their explicit PDF target, not an abstract.
        url = candidate.fulltext_url if candidate.source_type == "paper" else candidate.url
        if not url:
            raise AdapterError(
                "fetch",
                "fetch_target_missing",
                "Original document target is missing",
                False,
                "fetch",
            )
        download = await self._downloader.download(url)
        if candidate.source_type == "paper" and download.media_type != "application/pdf":
            raise AdapterError(
                "fetch", "fetch_response_invalid", "Paper original must be a PDF", False, "fetch"
            )
        if len(download.body) > self._config.max_content_bytes:
            raise AdapterError(
                "fetch",
                "resource_limit",
                "Original document exceeds configured limit",
                False,
                "fetch",
            )
        # Never accept fabricated hashes even from an injected downloader.
        digest = hashlib.sha256(download.body).hexdigest()
        if digest != download.sha256:
            raise AdapterError(
                "fetch",
                "content_hash_mismatch",
                "Downloaded bytes failed integrity validation",
                False,
                "fetch",
            )
        reference = await self._content.put(
            self._prefix + digest,
            download.body,
            media_type=download.media_type,
            expected_hash=digest,
        )
        if (
            reference.key != self._prefix + digest
            or reference.sha256 != digest
            or reference.size != len(download.body)
            or reference.media_type != download.media_type
        ):
            raise AdapterError(
                "fetch",
                "content_hash_mismatch",
                "Original content reference differs from downloaded bytes",
                False,
                "fetch",
            )
        parsed = ParsedDocument.model_validate(await self._parser.parse(reference, self._config))
        if (
            parsed.input_hash != digest
            or parsed.parser_version != self._config.parser_version
            or len(parsed.blocks) > self._config.max_blocks
        ):
            raise AdapterError(
                "parser",
                "parser_output_invalid",
                "Parser output differs from original/configuration",
                False,
                "parse",
            )
        body = json.dumps(
            parsed.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        parsed_hash = hashlib.sha256(body).hexdigest()
        parsed_reference = await self._content.put(
            self._prefix + parsed_hash,
            body,
            media_type="application/json",
            expected_hash=parsed_hash,
        )
        if (
            parsed_reference.key != self._prefix + parsed_hash
            or parsed_reference.sha256 != parsed_hash
            or parsed_reference.size != len(body)
            or parsed_reference.media_type != "application/json"
        ):
            raise AdapterError(
                "fetch",
                "content_hash_mismatch",
                "Parser content reference differs from serialized bytes",
                False,
                "fetch",
            )
        return FetchedDocument(
            content_ref=reference,
            parsed_content_ref=parsed_reference,
            final_url=download.final_url,
            media_type=download.media_type,
            hash=digest,
            locations=[block.location for block in parsed.blocks],
            parser_version=parsed.parser_version,
        )

    async def read_parsed(self, fetched: FetchedDocument) -> ParsedDocument:
        """Read existing immutable mapping; never redownload/reparse on a cache miss."""
        fetched = FetchedDocument.model_validate(fetched)
        if not fetched.content_ref.key.startswith(
            self._prefix
        ) or not fetched.parsed_content_ref.key.startswith(self._prefix):
            raise AdapterError(
                "fetch", "content_scope_mismatch", "Content belongs to another Run", False, "fetch"
            )
        stream = await self._content.get(fetched.parsed_content_ref.key)
        body = bytearray()
        async for chunk in stream:
            if len(body) + len(chunk) > 50 * 1024 * 1024:
                raise AdapterError(
                    "fetch",
                    "resource_limit",
                    "Stored parsed document exceeds limit",
                    False,
                    "fetch",
                )
            body.extend(chunk)
        if (
            hashlib.sha256(body).hexdigest() != fetched.parsed_content_ref.sha256
            or len(body) != fetched.parsed_content_ref.size
        ):
            raise AdapterError(
                "fetch",
                "content_hash_mismatch",
                "Parsed bytes failed integrity validation",
                False,
                "fetch",
            )
        parsed = ParsedDocument.model_validate_json(body)
        if (
            parsed.input_hash != fetched.hash
            or parsed.parser_version != fetched.parser_version
            or parsed.parser_version != self._config.parser_version
            or [block.location for block in parsed.blocks] != fetched.locations
        ):
            raise AdapterError(
                "parser",
                "parser_output_invalid",
                "Stored parser mapping differs from fetched document",
                False,
                "parse",
            )
        digest, size = hashlib.sha256(), 0
        async for chunk in await self._content.get(fetched.content_ref.key):
            size += len(chunk)
            if size > self._config.max_content_bytes:
                raise AdapterError(
                    "fetch",
                    "resource_limit",
                    "Stored original exceeds configured limit",
                    False,
                    "fetch",
                )
            digest.update(chunk)
        if digest.hexdigest() != fetched.hash or size != fetched.content_ref.size:
            raise AdapterError(
                "fetch",
                "content_hash_mismatch",
                "Stored original failed integrity validation",
                False,
                "fetch",
            )
        return parsed
