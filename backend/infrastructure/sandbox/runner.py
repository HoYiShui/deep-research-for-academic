"""Container-only supervisor; bounded tmpfs outputs become a byte envelope."""

import base64
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024


def reject_constant(_):
    raise ValueError("Nonfinite JSON is forbidden")


def read_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise ValueError("Invalid output file")
        body = stream.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("Output exceeds limit")
        return body


def main():
    child = subprocess.run(
        [sys.executable, "-I", "-B", "/work/script.py"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if child.returncode:
        return 1
    output_directory = Path("/work/out")
    raw = read_file(output_directory / "result.json")
    output = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(output, dict):
        return 1
    names = output.get("files", [])
    if not isinstance(names, list) or len(names) > 16:
        return 1
    if any(
        not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
        for name in names
    ):
        return 1
    if len(set(names)) != len(names) or "result.json" in names:
        return 1
    if {item.name for item in output_directory.iterdir()} != {"result.json", *names}:
        return 1
    files, size = [], len(raw)
    for name in names:
        body = read_file(output_directory / name)
        size += len(body)
        if size > MAX_BYTES:
            return 1
        files.append({"name": name, "body": base64.b64encode(body).decode("ascii")})
    envelope = json.dumps({"output": output, "files": files}, allow_nan=False).encode()
    if len(envelope) > 16 * 1024 * 1024:
        return 1
    sys.stdout.buffer.write(envelope)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, RecursionError):
        # Output can contain secrets: never return traceback or template logs.
        sys.exit(1)
