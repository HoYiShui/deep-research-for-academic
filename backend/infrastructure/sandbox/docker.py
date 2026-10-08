"""Development trusted-script kernel, not the formal AnalysisSpec template gate.

T031 owns closed compilation. Never pass model-generated code to this kernel.
The legacy execute facade returns output only; execute_checked retains verified
file bytes for a future formal adapter without persisting arbitrary object keys.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from domain.ports import AdapterError

DEFAULT_IMAGE = (
    "mirror.gcr.io/library/python@sha256:"
    "05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f"
)
MAX_OUTPUT = 10 * 1024 * 1024
MAX_ENVELOPE = 16 * 1024 * 1024
MEDIA = {".png": "image/png", ".json": "application/json", ".csv": "text/csv"}


def failure(code, message, *, retryable=False):
    return AdapterError("sandbox", code, message, retryable, "execute")


def reject_constant(_):
    raise ValueError("Nonfinite JSON is forbidden")


@dataclass(frozen=True)
class SandboxFile:
    name: str
    media_type: str
    sha256: str
    size: int
    body: bytes


@dataclass(frozen=True)
class SandboxResult:
    output: dict
    files: tuple[SandboxFile, ...]


def decode_result(body: bytes) -> SandboxResult:
    """Recheck the untrusted envelope before exposing any attachment bytes."""
    try:
        value = json.loads(body, parse_constant=reject_constant)
        if set(value) != {"output", "files"} or not isinstance(value["output"], dict):
            raise ValueError
        items, names = value["files"], value["output"].get("files", [])
        if not isinstance(items, list) or len(items) > 16 or not isinstance(names, list):
            raise ValueError
        files, seen = [], set()
        size = len(json.dumps(value["output"], ensure_ascii=False).encode())
        for item in items:
            if set(item) != {"name", "body"}:
                raise ValueError
            name = item["name"]
            if not isinstance(name, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name
            ):
                raise ValueError
            media = MEDIA.get(Path(name).suffix.lower())
            if name in seen or name == "result.json" or media is None:
                raise ValueError
            raw = base64.b64decode(item["body"], validate=True)
            if media == "image/png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError
            if media == "application/json":
                json.loads(raw, parse_constant=reject_constant)
            elif media == "text/csv":
                raw.decode("utf-8")
            size += len(raw)
            if size > MAX_OUTPUT:
                raise ValueError
            files.append(SandboxFile(name, media, hashlib.sha256(raw).hexdigest(), len(raw), raw))
            seen.add(name)
        if (
            names != [item.name for item in files]
            or len(set(names)) != len(names)
            or size > MAX_OUTPUT
        ):
            raise ValueError
        return SandboxResult(value["output"], tuple(files))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise failure(
            "output_invalid", "Sandbox output failed size, path, media or JSON validation"
        ) from None


class DockerExecution:
    def __init__(self, *, image=DEFAULT_IMAGE):
        self.image = image

    async def execute(self, code: str, input_data: dict, timeout_s: int = 30) -> dict:
        return (await self.execute_checked(code, input_data, timeout_s)).output

    async def execute_checked(
        self, code: str, input_data: dict, timeout_s: int = 30
    ) -> SandboxResult:
        if not isinstance(code, str) or not code.strip() or len(code.encode()) > 256 * 1024:
            raise failure("input_invalid", "Sandbox requires a bounded trusted script")
        if type(input_data) is not dict or type(timeout_s) is not int or not 1 <= timeout_s <= 300:
            raise failure("input_invalid", "Invalid sandbox input or timeout")
        try:
            encoded = json.dumps(input_data, allow_nan=False).encode()
        except (ValueError, TypeError):
            raise failure("input_invalid", "Sandbox input must be finite JSON") from None
        if len(encoded) > MAX_OUTPUT:
            raise failure("input_invalid", "Sandbox input exceeds 10 MiB")
        process = None
        with tempfile.TemporaryDirectory(prefix="dr4a-sandbox-") as temporary:
            directory = Path(temporary)
            directory.chmod(0o755)
            script, inputs, cidfile = (
                directory / "script.py",
                directory / "input.json",
                directory / "cid",
            )
            script.write_text(code)
            inputs.write_bytes(encoded)
            script.chmod(0o444)
            inputs.chmod(0o444)
            runner = Path(__file__).with_name("runner.py").resolve()
            command = [
                "docker",
                "run",
                "--pull=never",
                "--cidfile",
                str(cidfile),
                "--network=none",
                "--read-only",
                "--memory=256m",
                "--memory-swap=256m",
                "--cpus=1",
                "--pids-limit=64",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=65534:65534",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m",
                "--tmpfs",
                "/work/out:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534,mode=0700",
                "--mount",
                f"type=bind,src={script},dst=/work/script.py,readonly",
                "--mount",
                f"type=bind,src={inputs},dst=/work/input.json,readonly",
                "--mount",
                f"type=bind,src={runner},dst=/work/runner.py,readonly",
                "--label",
                "dr4a.sandbox=true",
                self.image,
                "python",
                "-I",
                "-B",
                "/work/runner.py",
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
                )
                async with asyncio.timeout(timeout_s):
                    body = bytearray()
                    while chunk := await process.stdout.read(65536):
                        body.extend(chunk)
                        if len(body) > MAX_ENVELOPE:
                            raise failure("output_invalid", "Sandbox envelope exceeds limit")
                    await process.wait()
                if process.returncode:
                    raise failure(
                        "execution_failed", "Sandbox process failed or its output was rejected"
                    )
                return decode_result(bytes(body))
            except TimeoutError:
                raise failure(
                    "execution_timeout", "Sandbox deadline exceeded", retryable=True
                ) from None
            except OSError:
                raise failure(
                    "dependency_unavailable", "Docker sandbox is unavailable", retryable=True
                ) from None
            finally:
                # The CID file is written by Docker, outside all container mounts.
                try:
                    if cidfile.is_file():
                        cid = cidfile.read_text().strip()
                        if not re.fullmatch(r"[a-f0-9]{64}", cid):
                            raise failure("cleanup_failed", "Invalid Docker container identity")
                        cleanup = await asyncio.create_subprocess_exec(
                            "docker",
                            "rm",
                            "--force",
                            cid,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        try:
                            await asyncio.wait_for(cleanup.wait(), timeout=10)
                        except TimeoutError:
                            cleanup.kill()
                            await cleanup.wait()
                            raise failure(
                                "cleanup_failed", "Docker container cleanup timed out"
                            ) from None
                        if cleanup.returncode:
                            raise failure("cleanup_failed", "Docker container cleanup failed")
                finally:
                    if process is not None and process.returncode is None:
                        process.kill()
                        await process.wait()
