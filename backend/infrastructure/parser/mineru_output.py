"""Normalize MinerU 4.0 Content List V1 without inventing text or PDF pages.

This pure boundary is not evidence that MinerU inference has run. The caller
must validate the producing package, PDF hash, full-document flag and pages.
"""

import json

from domain.documents import ParsedBlock, ParsedDocument, ParserConfig
from domain.ports import AdapterError
from domain.research.facts import Location

MINERU_PACKAGE_VERSION = "4.0.10"
MINERU_PARSER_VERSION = "dr4a-mineru-4.0.10-standard-v1"
MODEL_REVISIONS = {
    "opendatalab/MinerU-4_models_torch": "2b3afb86f4d23fa623f6b2f4b2279ed4ef93a89d",
    "jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF": "9185688a0495e1577d521a757c7c0b62dd38ca48",
}


def error(code):
    return AdapterError("parser", code, "PDF structure validation failed", False, "parse")


def strings(item, key):
    value = item.get(key, [])
    if type(value) is not list or any(type(text) is not str for text in value):
        raise error("parser_output_invalid")
    return [text for text in value if text.strip()]


def content_list_document(items, *, input_hash, page_count, config):
    config = ParserConfig.model_validate(config)
    if config.parser_version != MINERU_PARSER_VERSION:
        raise error("parser_version_mismatch")
    if type(page_count) is not int or page_count < 1:
        raise error("invalid_document")
    if page_count > config.max_pages:
        raise error("resource_limit")
    if type(items) is not list or len(items) > config.max_blocks:
        raise error("resource_limit" if type(items) is list else "parser_output_invalid")
    blocks, section, previous_page = [], None, -1
    for index, item in enumerate(items):
        if type(item) is not dict:
            raise error("parser_output_invalid")
        page = item.get("page_idx")
        if type(page) is not int or not 0 <= page < page_count:
            raise error("parser_output_invalid")
        if page < previous_page:
            raise error("parser_output_invalid")
        previous_page = page
        kind, caption, notes = item.get("type"), None, []
        if kind == "table":
            content, block_type = item.get("table_body"), "table"
            caption = "\n".join(strings(item, "table_caption")) or None
            notes = strings(item, "table_footnote")
        elif kind == "equation":
            content, block_type = item.get("text"), "formula"
        elif kind in {"list", "index"}:
            content, block_type = "\n".join(strings(item, "list_items")), "text"
        elif kind == "code":
            content, block_type = item.get("code_body"), "text"
            caption = "\n".join(strings(item, "code_caption")) or None
            notes = strings(item, "code_footnote")
        elif kind in {"image", "chart"}:
            # Captions are real text, not a transcription of image-only values.
            content = "\n".join(strings(item, f"{kind}_caption"))
            content += "\n" + "\n".join(strings(item, f"{kind}_footnote"))
            block_type = "text"
            if not content.strip():
                continue
        elif kind in {"text", "page_footnote", "header", "footer", "page_number", "aside_text"}:
            content, block_type = item.get("text"), "text"
            if item.get("text_level") is not None:
                level = item["text_level"]
                if type(level) is not int or not 0 <= level <= 6:
                    raise error("parser_output_invalid")
                if type(content) is str and content.strip():
                    section = content
        else:
            raise error("parser_output_invalid")
        if type(content) is not str or not content.strip():
            if block_type == "text" and type(content) is str:
                continue
            # An image-only table/formula must not become invented or empty text.
            raise AdapterError(
                "parser",
                "parser_output_incomplete",
                f"PDF {block_type} block lacks structured content",
                False,
                "parse",
            )
        try:
            blocks.append(
                ParsedBlock(
                    type=block_type,
                    content=content,
                    caption=caption,
                    notes=notes,
                    location=Location(
                        page_start=page + 1,
                        page_end=page + 1,
                        section=section,
                        selector=f"mineru:content-list:{index}",
                    ),
                )
            )
        except ValueError as exc:
            raise error("parser_output_invalid") from exc
    if not blocks:
        raise error("parse_empty")
    document = ParsedDocument(
        blocks=blocks, input_hash=input_hash, parser_version=config.parser_version
    )
    if (
        len(json.dumps(document.model_dump(mode="json"), ensure_ascii=False).encode())
        > config.max_content_bytes
    ):
        raise error("resource_limit")
    return document
