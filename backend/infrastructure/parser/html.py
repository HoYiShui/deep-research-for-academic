"""Deterministic UTF-8 HTML/text normalizer; source lines, no model/OCR guesses.

Tables retain exact original markup (including spans, headers, captions and
notes). Normalizing them into numeric grids is not this parser's authority.
PDF support remains the local versioned MinerU task, never a text fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from application.ports import ContentStorePort
from domain.content import ContentRef
from domain.documents import ParsedBlock, ParsedDocument, ParserConfig
from domain.ports import AdapterError
from domain.research.facts import Location

HTML_PARSER_VERSION = "dr4a-html-v1"
# HTML/text plus the PDF text layer (per-page paragraphs, no table/formula
# structure). A light local alternative to MinerU for public paper research.
LIGHT_PARSER_VERSION = "dr4a-light-v1"
PARSER_VERSIONS = frozenset({HTML_PARSER_VERSION, LIGHT_PARSER_VERSION})
_BREAK = {
    "p",
    "li",
    "div",
    "article",
    "section",
    "blockquote",
    "pre",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}
_HIDDEN = {"script", "style", "noscript", "template"}


def parse_error(code):
    return AdapterError("parser", code, "Document cannot be parsed", False, "parse")


class _HTML(HTMLParser):
    def __init__(self, source, config):
        super().__init__(convert_charrefs=True)
        self.source, self.config = source, config
        self.blocks, self.parts = [], []
        self.start, self.end, self.hidden = None, 1, 0
        self.section = None
        self.heading = False
        self.pre_depth = 0
        self.raw_tag = None
        self.table_depth, self.table_count = 0, 0
        self.table_start, self.table_line = None, None
        self.offsets = [0]
        for line in source.split("\n"):
            self.offsets.append(self.offsets[-1] + len(line) + 1)

    def source_index(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def append(self, block):
        if len(self.blocks) >= self.config.max_blocks:
            raise parse_error("resource_limit")
        self.blocks.append(block)

    def flush(self):
        content = "".join(self.parts)
        if not self.pre_depth:
            content = " ".join(content.split())
        if content.strip():
            if len(content) > 2 * 1024 * 1024:
                raise parse_error("resource_limit")
            if self.heading:
                self.section = content
            self.append(
                ParsedBlock(
                    type="text",
                    content=content,
                    location=Location(
                        line_start=self.start,
                        line_end=max(self.end, self.start),
                        section=self.section,
                    ),
                )
            )
        self.parts, self.start, self.heading = [], None, False

    def handle_starttag(self, tag, attrs):
        if tag in _HIDDEN:
            self.hidden += 1
        if self.hidden:
            return
        if tag in {"table", "math"} and (self.raw_tag is None or tag == self.raw_tag):
            if not self.table_depth:
                self.flush()
                self.raw_tag = tag
                if tag == "table":
                    self.table_count += 1
                self.table_start, self.table_line = self.source_index(), self.getpos()[0]
            self.table_depth += 1
        if self.table_depth:
            return
        if tag == "pre":
            self.flush()
            self.pre_depth += 1
            return
        if tag in {"sup", "sub", "del", "s", "strike", "ins"}:
            if self.start is None:
                self.start = self.getpos()[0]
            self.parts.append(self.get_starttag_text())
        if self.pre_depth:
            if tag == "br":
                self.parts.append("\n")
            return
        if tag in _BREAK:
            self.flush()
            self.heading = tag in {"h1", "h2", "h3", "h4", "h5", "h6"}

    def handle_startendtag(self, tag, attrs):
        # Void elements cannot leave a suppressed or table scope open.
        if tag in _BREAK and not self.hidden and not self.table_depth:
            if self.pre_depth:
                if tag == "br":
                    self.parts.append("\n")
            else:
                self.flush()

    def handle_endtag(self, tag):
        if tag in _HIDDEN and self.hidden:
            self.hidden -= 1
            return
        if self.hidden:
            return
        if tag == self.raw_tag and self.table_depth:
            self.table_depth -= 1
            if not self.table_depth:
                index = self.source_index()
                end = self.source.find(">", index)
                if end < 0:
                    raise parse_error("invalid_document")
                content = self.source[self.table_start : end + 1]
                if len(content) > 2 * 1024 * 1024:
                    raise parse_error("resource_limit")
                self.append(
                    ParsedBlock(
                        type="table" if tag == "table" else "formula",
                        content=content,
                        location=Location(
                            line_start=self.table_line,
                            line_end=self.getpos()[0],
                            table=f"table_{self.table_count}" if tag == "table" else None,
                            section=self.section,
                        ),
                    )
                )
                self.raw_tag = None
            return
        if self.table_depth:
            return
        if tag == "pre" and self.pre_depth:
            self.flush()
            self.pre_depth -= 1
            return
        if tag in {"sup", "sub", "del", "s", "strike", "ins"} and self.parts:
            start = self.source_index()
            end = self.source.find(">", start)
            self.parts.append(self.source[start : end + 1])
            self.end = max(self.end, self.getpos()[0])
        if self.pre_depth:
            return
        if not self.table_depth and tag in _BREAK:
            self.flush()

    def handle_data(self, data):
        if self.hidden or self.table_depth or not data:
            return
        if self.start is None:
            self.start = self.getpos()[0]
        # Character references may decode to newlines which did not exist in
        # the original HTML. Count SOURCE newlines, not normalized data lines.
        offset = self.source_index()
        stop = self.source.find("<", offset)
        stop = len(self.source) if stop < 0 else stop
        self.end = max(self.end, self.getpos()[0] + self.source[offset:stop].count("\n"))
        self.parts.append(data)

    def finish(self):
        self.feed(self.source)
        self.close()
        if self.table_depth or self.pre_depth:
            raise parse_error("invalid_document")
        self.flush()
        if not self.blocks:
            raise parse_error("parse_empty")
        return self.blocks


def _media_types(config):
    if config.parser_version == LIGHT_PARSER_VERSION:
        return {"text/html", "text/plain", "application/pdf"}
    return {"text/html", "text/plain"}


def _pdf_blocks(body, config):
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(body)
    except pypdfium2.PdfiumError as exc:
        raise parse_error("invalid_document") from exc
    try:
        if len(document) > config.max_pages:
            raise parse_error("resource_limit")
        blocks = []
        for index in range(len(document)):
            page = document[index]
            try:
                text = page.get_textpage().get_text_range()
            finally:
                page.close()
            for paragraph in _paragraphs(text):
                if len(blocks) >= config.max_blocks:
                    raise parse_error("resource_limit")
                blocks.append(
                    ParsedBlock(
                        type="text",
                        content=paragraph,
                        location=Location(page_start=index + 1, page_end=index + 1),
                    )
                )
    finally:
        document.close()
    if not blocks:
        raise parse_error("parse_empty")  # Scanned PDFs have no text layer.
    return blocks


def _paragraphs(text):
    """Join PDF layout lines into paragraphs at blank lines or sentence ends."""
    # pdfium marks soft hyphens with U+FFFE; they are layout, not content.
    text = text.replace("\ufffe", "").replace("\u00ad", "")
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    paragraph, result = [], []
    for line in [*lines, ""]:
        if line:
            if paragraph and paragraph[-1].endswith("-") and line[:1].islower():
                paragraph[-1] = paragraph[-1][:-1] + line
            else:
                paragraph.append(line)
        if paragraph and (not line or (len(" ".join(paragraph)) > 400 and line.endswith("."))):
            result.append(" ".join(paragraph))
            paragraph = []
    return [item for item in result if len(item) >= 20 or any(c.isdigit() for c in item)]


def normalize(body, reference, config):
    if config.parser_version not in PARSER_VERSIONS:
        raise parse_error("parser_version_mismatch")
    if reference.media_type not in _media_types(config):
        raise parse_error("parser_not_configured")
    if len(body) != reference.size or hashlib.sha256(body).hexdigest() != reference.sha256:
        raise parse_error("content_hash_mismatch")
    if len(body) > config.max_content_bytes:
        raise parse_error("resource_limit")
    if reference.media_type == "application/pdf":
        if not body.startswith(b"%PDF-"):
            raise parse_error("invalid_document")
        return ParsedDocument(
            blocks=_pdf_blocks(body, config),
            input_hash=reference.sha256,
            parser_version=config.parser_version,
        )
    try:
        text = body.decode("utf-8-sig")
        if "\x00" in text or body.startswith(b"%PDF-"):
            raise parse_error("invalid_document")
        if reference.media_type == "text/html":
            blocks = _HTML(text, config).finish()
        else:
            blocks = []
            for line, content in enumerate(text.split("\n"), 1):
                if content.strip():
                    if len(blocks) >= config.max_blocks or len(content) > 2 * 1024 * 1024:
                        raise parse_error("resource_limit")
                    blocks.append(
                        ParsedBlock(
                            type="text",
                            content=content,
                            location=Location(line_start=line, line_end=line),
                        )
                    )
            if not blocks:
                raise parse_error("parse_empty")
        return ParsedDocument(
            blocks=blocks, input_hash=reference.sha256, parser_version=config.parser_version
        )
    except (ValueError, UnicodeError) as exc:
        raise parse_error("invalid_document") from exc


class HTMLDocumentParser:
    def __init__(self, content: ContentStorePort):
        self._content = content
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="html-parser")
        self._slots = asyncio.Semaphore(1)
        self._closed = False

    async def parse(self, reference: ContentRef, config: ParserConfig) -> ParsedDocument:
        reference = ContentRef.model_validate(reference)
        config = ParserConfig.model_validate(config)
        if config.parser_version not in PARSER_VERSIONS:
            raise parse_error("parser_version_mismatch")
        if reference.media_type not in _media_types(config):
            raise parse_error("parser_not_configured")
        if reference.size > config.max_content_bytes:
            raise parse_error("resource_limit")
        await self._slots.acquire()
        try:
            if self._closed:
                raise parse_error("parser_not_configured")
            body = bytearray()
            async for chunk in await self._content.get(reference.key):
                if len(body) + len(chunk) > config.max_content_bytes:
                    raise parse_error("resource_limit")
                body.extend(chunk)
            future = asyncio.get_running_loop().run_in_executor(
                self._executor, normalize, bytes(body), reference, config
            )
        except BaseException:
            self._slots.release()
            raise

        def finished(value):
            self._slots.release()
            if not value.cancelled():
                value.exception()

        future.add_done_callback(finished)
        return await asyncio.shield(future)

    async def close(self):
        self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
