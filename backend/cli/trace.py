"""Exclusive, owner-readable JSONL debug export; never a durable fact source."""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from cli import output

_SECRET_KEY = re.compile(
    r"password|secret|api[_-]?key|authorization|access[_-]?token", re.IGNORECASE
)
_CREDENTIAL = re.compile(r"(?i)(Bearer\s+|(?:api[_-]?key|token|password|secret)=)[^\s&\"<>]+")
_USERINFO = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+:[^/@\s]+@")


class TraceRecorder:
    def __init__(self, path, *, content=False, secrets=()):
        self.path = str(Path(path).absolute())
        self.content, self.events, self.error = content, 0, None
        self.secrets = sorted({value for value in secrets if value}, key=len, reverse=True)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.file = os.fdopen(fd, "w", encoding="utf-8")
        except OSError:
            raise output.UsageError(
                "Trace file cannot be created; use a new writable path"
            ) from None

    def _redact(self, value):
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if _SECRET_KEY.search(key) else self._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            return _USERINFO.sub(r"\1[REDACTED]@", _CREDENTIAL.sub(r"\1[REDACTED]", value))
        return value

    def __call__(self, record, *, content=None):
        if self.error:
            return
        value = record | {"trace_seq": self.events + 1, "timestamp": datetime.now(UTC).isoformat()}
        if self.content and content is not None:
            value["content"] = content
        try:
            line = json.dumps(self._redact(value), ensure_ascii=False, allow_nan=False)
            if (
                len(line.encode()) > 2 * 1024 * 1024
                or self.file.tell() + len(line.encode()) + 1 > 64 * 1024 * 1024
            ):
                self.error = "trace_size_limit"
                return
            self.file.write(line + "\n")
            self.file.flush()
            self.events += 1
        except (OSError, ValueError, TypeError):
            self.error = "trace_write_failed"

    def close(self):
        try:
            self.file.close()
        except OSError:
            self.error = "trace_write_failed"
        if self.error:
            output.log(f"trace_incomplete code={self.error} records={self.events}")
