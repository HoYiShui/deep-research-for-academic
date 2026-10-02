"""Docker sandbox adapter (one-shot hardened container)."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path


class DockerExecution:
    """CodeExecutionPort implementation via a one-shot Docker container."""

    async def execute(self, code: str, input_data: dict, timeout_s: int = 30) -> dict:
        """Run code in an isolated container and return its output.

        Args:
            code: The script to execute.
            input_data: Input passed to the script via /work/input.json.
            timeout_s: Execution timeout in seconds.

        Returns:
            The parsed /work/out/result.json, or {} on failure.
        """
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "input.json").write_text(json.dumps(input_data))
            (d / "script.py").write_text(code)
            (d / "out").mkdir()
            cmd = [
                "docker", "run", "--rm",
                "--network=none",
                "--read-only",
                "--memory=256m", "--cpus=1",
                "--pids-limit=64",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=65534:65534",
                "--tmpfs", "/tmp",
                "-v", f"{d}/input.json:/work/input.json:ro",
                "-v", f"{d}/out:/work/out",
                "python:3.12-slim", "python", "/work/script.py",
            ]
            proc = await asyncio.create_subprocess_exec(*cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                return {}
            result = d / "out" / "result.json"
            if result.exists():
                return json.loads(result.read_text())
        return {}
