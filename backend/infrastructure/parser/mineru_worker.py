"""Owned subprocess entrypoint: local weights only, no caller secrets/config."""

import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

from domain.documents import ParserConfig
from infrastructure.parser.mineru_output import (
    MINERU_PACKAGE_VERSION,
    MINERU_PARSER_VERSION,
    MODEL_REVISIONS,
    content_list_document,
    error,
)


def parse_local(path, models, config):
    if importlib.metadata.version("mineru") != MINERU_PACKAGE_VERSION:
        raise error("parser_version_mismatch")
    import pypdfium2

    try:
        with pypdfium2.PdfDocument(path) as pdf:
            count = len(pdf)
    except Exception as exc:
        raise error("invalid_document") from exc
    if count < 1 or count > config.max_pages:
        raise error("resource_limit" if count else "parse_empty")
    root = Path(models).resolve()
    manifest = json.loads((root / "dr4a-models.json").read_text())
    if (manifest.get("package_version"), manifest.get("parser_version")) != (
        MINERU_PACKAGE_VERSION,
        MINERU_PARSER_VERSION,
    ):
        raise error("parser_version_mismatch")
    records = manifest["repositories"]
    if {r["repo"]: r["revision"] for r in records} != MODEL_REVISIONS:
        raise error("parser_version_mismatch")
    for record in records:
        for item in record["files"]:
            target = (root / item["path"]).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise error("parser_not_configured")
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if target.stat().st_size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise error("content_hash_mismatch")
    from mineru.config import VlmConfig
    from mineru.config import config as runtime
    from mineru.parser import MinerUParser
    from mineru.render import render_content_list

    runtime.model.base_dir = str(root)
    runtime.model.source = "local"
    runtime.model.small_backend = "torch"
    runtime.model.vlm = VlmConfig(engine="llama-cpp", server_url="")
    runtime.llm_aided.features.title_leveling = False
    runtime.llm_aided.features.cross_page_table_cell_merge = False
    with MinerUParser(tier="standard", vlm_config=runtime.model.vlm) as parser:
        result = parser.parse(path, page_range="all")
    if not result.middle_json.is_full_document or [p.page_idx for p in result.pages] != list(
        range(count)
    ):
        from domain.ports import AdapterError

        raise AdapterError(
            "parser", "parser_output_incomplete", "PDF page mapping is incomplete", False, "parse"
        )
    original = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return content_list_document(
        render_content_list(result.middle_json),
        input_hash=original,
        page_count=count,
        config=config,
    )


def main():
    from domain.ports import AdapterError

    path, models, config_file, output = sys.argv[1:]
    try:
        config = ParserConfig.model_validate_json(Path(config_file).read_text())
        result = parse_local(path, models, config)
        record = {"status": "ok", "document": result.model_dump(mode="json")}
    except AdapterError as exc:
        record = {"status": "error", "code": exc.code, "message": exc.message}
    except (FileNotFoundError, ImportError):
        record = {"status": "error", "code": "parser_not_configured"}
    except Exception:  # noqa: BLE001 -- never export SDK details or private content.
        record = {"status": "error", "code": "parser_failed"}
    Path(output).write_text(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
