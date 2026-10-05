"""Application failures without HTTP or adapter SDK dependencies."""

from __future__ import annotations


class AppError(Exception):
    def __init__(
        self, code: str, message: str, *, retryable: bool = False, details: dict | None = None
    ):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details
