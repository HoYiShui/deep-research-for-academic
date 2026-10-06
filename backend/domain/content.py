"""Immutable, content-addressed objects used by persisted tool results.

This narrow cache port is not the document lifecycle ContentStore contract.
"""

from typing import Protocol

from domain.research.models import Hash, Nonnegative, Record, Text


class ContentRef(Record):
    key: Text
    sha256: Hash
    size: Nonnegative
    media_type: Text


class ResultCachePort(Protocol):
    async def put(self, namespace: str, content: bytes, media_type: str) -> ContentRef: ...

    async def read(self, reference: ContentRef) -> bytes: ...
