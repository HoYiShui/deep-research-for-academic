"""Legacy content/parser interfaces, fail-closed until their migration tasks.

The text-only Fetch bridge delegates to the restricted downloader; it does
not implement the formal versioned FetchedDocument/Parser/content contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from domain.documents import ParsedDocument, ParserConfig
from domain.ports import AdapterError
from infrastructure.fetch.http import RestrictedDownloader
from infrastructure.parser.mineru_output import MINERU_PARSER_VERSION, error


class MinerUDocumentParser:
    """Formal content-reference Parser with an owned, killable local subprocess.

    No SDK global state, API credentials, caller MinerU config or automatic model
    downloads are inherited. Originals are bounded/hash-checked before inference.
    """

    def __init__(self, content, models_dir, *, timeout_s=600):
        if timeout_s <= 0:
            raise ValueError("A positive Parser timeout is required")
        self._content, self._models = content, str(Path(models_dir).resolve())
        self._timeout, self._closed = timeout_s, False
        self._slot, self._processes = asyncio.Semaphore(1), set()

    async def _stop(self, process):
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await process.wait()

    async def parse(self, reference, config):
        try:
            return await self._parse(reference, config)
        except TimeoutError as exc:
            raise AdapterError(
                "parser", "parser_timeout", "Local Parser timed out", True, "parse"
            ) from exc

    async def _parse(self, reference, config):
        from domain.content import ContentRef

        reference, config = (
            ContentRef.model_validate(reference),
            ParserConfig.model_validate(config),
        )
        if config.parser_version != MINERU_PARSER_VERSION:
            raise error("parser_version_mismatch")
        if reference.media_type != "application/pdf":
            raise error("invalid_document")
        if reference.size > config.max_content_bytes:
            raise error("resource_limit")
        async with self._slot:
            if self._closed:
                raise error("parser_not_configured")
            async with asyncio.timeout(self._timeout):
                with tempfile.TemporaryDirectory(prefix="dr4a-pdf-") as temporary:
                    root = Path(temporary)
                    original = root / "original.pdf"
                    digest, size = hashlib.sha256(), 0
                    with original.open("wb") as stream:
                        async for chunk in await self._content.get(reference.key):
                            size += len(chunk)
                            if size > config.max_content_bytes:
                                raise error("resource_limit")
                            digest.update(chunk)
                            stream.write(chunk)
                    if digest.hexdigest() != reference.sha256 or size != reference.size:
                        raise error("content_hash_mismatch")
                    (root / "input.json").write_text(config.model_dump_json())
                    (root / "local.json").write_text(
                        json.dumps(
                            {
                                "model": {
                                    "base_dir": self._models,
                                    "source": "local",
                                    "small_backend": "torch",
                                    "vlm": {"engine": "llama-cpp", "server_url": ""},
                                }
                            }
                        )
                    )
                    env = {
                        "PATH": os.environ.get("PATH", ""),
                        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                        "MINERU_HOME": temporary,
                        "MINERU_CONFIG": str(root / "local.json"),
                        "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1",
                    }
                    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
                        raise error("parser_isolation_not_configured")
                    process = await asyncio.create_subprocess_exec(
                        "sandbox-exec",
                        "-p",
                        "(version 1)(allow default)(deny network*)",
                        sys.executable,
                        "-m",
                        "infrastructure.parser.mineru_worker",
                        str(original),
                        self._models,
                        str(root / "input.json"),
                        str(root / "output.json"),
                        cwd=temporary,
                        env=env,
                        start_new_session=True,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    self._processes.add(process)
                    try:
                        await process.wait()
                    finally:
                        await self._stop(process)
                        self._processes.discard(process)
                    output = root / "output.json"
                    if process.returncode or not output.is_file():
                        raise error("parser_failed")
                    if output.stat().st_size > config.max_content_bytes:
                        raise error("resource_limit")
                    record = json.loads(output.read_text())
                    if record.get("status") != "ok":
                        code = record.get("code")
                        allowed = {
                            "parser_version_mismatch",
                            "invalid_document",
                            "resource_limit",
                            "parse_empty",
                            "parser_output_invalid",
                            "parser_output_incomplete",
                            "parser_not_configured",
                            "content_hash_mismatch",
                            "parser_failed",
                        }
                        messages = {
                            "PDF page mapping is incomplete",
                            "PDF table block lacks structured content",
                            "PDF formula block lacks structured content",
                            "PDF text block lacks structured content",
                        }
                        if code == "parser_output_incomplete" and record.get("message") in messages:
                            raise AdapterError("parser", code, record["message"], False, "parse")
                        raise error(code if code in allowed else "parser_failed")
                    parsed = ParsedDocument.model_validate(record["document"])
                    if (
                        parsed.input_hash != reference.sha256
                        or parsed.parser_version != config.parser_version
                    ):
                        raise error("parser_output_invalid")
                    return parsed

    async def close(self):
        self._closed = True
        for process in tuple(self._processes):
            await self._stop(process)


class MinerUParser:
    """Parse a PDF into structured text (body + tables + formulas)."""

    async def parse(self, path: str) -> dict:
        """Parse a PDF; returns {"text": "...", "tables": [...], "formulas": [...]}."""
        # Actual versioned local parser is implemented in T043; until then a
        # missing parser is a dependency failure, never successful empty text.
        raise AdapterError(
            "parser",
            "parser_not_configured",
            "Local document parser is not configured",
            False,
            "parse",
        )


class MinioContentStore:
    """Unmigrated legacy chunk interface; never pretend missing content is valid.

    Tool result persistence uses infrastructure.storage.content_cache instead.
    Document/chunk lifecycle storage is implemented in the ingestion tasks.
    """

    def __init__(self) -> None:
        self._bucket = None

    async def get(self, chunk_id: str) -> str:
        """Read a chunk's text from MinIO."""
        raise AdapterError(
            "minio",
            "content_store_not_configured",
            "Document content storage is not configured",
            False,
            "get",
        )


class HttpFetch:
    """Legacy text-only bridge; network safety uses the same restricted downloader.

    Formal FetchedDocument/Parser/content references are implemented separately;
    this compatibility interface must never decode PDF bytes as fake text.
    """

    def __init__(self, downloader: RestrictedDownloader | None = None):
        self._downloader = downloader if downloader is not None else RestrictedDownloader()

    async def fetch(self, source_type: str, doc_ref: str) -> str:
        if source_type != "web":
            raise AdapterError(
                "fetch", "fetch_parser_required", "Document parser is required", False, "fetch"
            )
        result = await self._downloader.download(doc_ref)
        if result.media_type not in {"text/html", "text/plain"}:
            raise AdapterError(
                "fetch", "fetch_parser_required", "Document parser is required", False, "fetch"
            )
        try:
            return result.body.decode("utf-8")
        except UnicodeError as exc:
            raise AdapterError(
                "fetch", "fetch_response_invalid", "Text encoding is unsupported", False, "fetch"
            ) from exc
