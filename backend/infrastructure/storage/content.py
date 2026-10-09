"""Shared 50MiB immutable document/research content, using one MinIO I/O driver.

Service-owned keys end in the actual byte hash. Thus a competing writer with
different bytes is rejected BEFORE S3 I/O, not via a racy stat-then-overwrite.
There are no mutable aliases or user-provided arbitrary paths. Ownership and
version/Run lifecycle remain the caller's application responsibility.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from collections.abc import AsyncIterable
from uuid import UUID

from domain.content import ContentRef
from domain.ports import AdapterError
from infrastructure.storage.content_cache import MinioObjectIO

MAX_CONTENT_BYTES = 50 * 1024 * 1024
_HASH = re.compile(r"^[a-f0-9]{64}$")
_MEDIA = re.compile(r"^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$")


def _ids(parts):
    try:
        return all(str(UUID(value)) == value for value in parts)
    except (ValueError, TypeError):
        return False


def content_key(key: str) -> str:
    if not isinstance(key, str):
        raise TypeError("Invalid content key")
    parts = key.split("/")
    if (
        (len(parts) == 4 and parts[0] == "documents" and _ids(parts[1:3]))
        or (len(parts) == 5 and parts[0] == "knowledge-content" and _ids(parts[1:4]))
        or (len(parts) == 3 and parts[0] == "research-content" and _ids(parts[1:2]))
    ) and _HASH.fullmatch(parts[-1]):
        return key
    raise ValueError("Content key requires document-version or Run scope and a byte hash")


def content_prefix(prefix: str) -> str:
    if not isinstance(prefix, str) or not prefix.endswith("/"):
        raise ValueError("A resource-scoped prefix ending in slash is required")
    parts = prefix[:-1].split("/")
    if (
        (parts[0] == "documents" and len(parts) in {2, 3} and _ids(parts[1:]))
        or (parts[0] == "knowledge-content" and len(parts) in {3, 4} and _ids(parts[1:]))
        or (parts[0] == "research-content" and len(parts) == 2 and _ids(parts[1:]))
    ):
        return prefix
    raise ValueError("Broad or malformed content deletion prefix is forbidden")


class MinioContentStore(MinioObjectIO):
    max_bytes = MAX_CONTENT_BYTES
    operation_name = "content_store"

    def __init__(self, *args, timeout_s: float = 30, **kwargs):
        super().__init__(*args, timeout_s=timeout_s, **kwargs)
        self._stream_timeout = timeout_s

    async def put(
        self,
        key: str,
        stream: AsyncIterable[bytes] | bytes,
        *,
        media_type: str,
        expected_hash: str | None = None,
    ) -> ContentRef:
        key = content_key(key)
        if not isinstance(media_type, str) or not _MEDIA.fullmatch(media_type):
            raise ValueError("Invalid content MIME type")
        if expected_hash is not None and (
            not isinstance(expected_hash, str) or not _HASH.fullmatch(expected_hash)
        ):
            raise ValueError("Invalid expected content hash")
        body = bytearray()
        async with asyncio.timeout(self._stream_timeout):
            if isinstance(stream, bytes):
                if len(stream) > self.max_bytes:
                    raise ValueError("Content exceeds 50MiB")
                body.extend(stream)
            else:
                async for chunk in stream:
                    if not isinstance(chunk, bytes):
                        raise TypeError("Content stream must yield bytes")
                    if len(body) + len(chunk) > self.max_bytes:
                        raise ValueError("Content exceeds 50MiB")
                    body.extend(chunk)
        digest = hashlib.sha256(body).hexdigest()
        if key.rsplit("/", 1)[-1] != digest or expected_hash not in (None, digest):
            raise AdapterError(
                "minio",
                "content_hash_mismatch",
                "Content key/hash differs from bytes",
                False,
                "content_store",
            )
        reference = ContentRef(key=key, sha256=digest, size=len(body), media_type=media_type)
        await self._io(self._put, reference, bytes(body))
        # Existing same-byte objects retain their original MIME, rather than
        # claiming the caller changed metadata without a write.
        return await self.head(key)

    async def head(self, key: str) -> ContentRef:
        return await self._io(self._head, content_key(key))

    def _head(self, key: str) -> ContentRef:
        stat = self._client.stat_object(self.bucket, key)
        if (
            stat.size > self.max_bytes
            or stat.size < 0
            or not _MEDIA.fullmatch(stat.content_type or "")
        ):
            raise self._corrupt()
        return ContentRef(
            key=key, sha256=key.rsplit("/", 1)[-1], size=stat.size, media_type=stat.content_type
        )

    async def read(self, reference: ContentRef) -> bytes:
        reference = ContentRef.model_validate(reference)
        key = content_key(reference.key)
        if key.rsplit("/", 1)[-1] != reference.sha256 or reference.size > self.max_bytes:
            raise ValueError("Invalid content reference")
        return await self._io(self._read, reference)

    async def get(self, key: str) -> AsyncIterable[bytes]:
        data = await self.read(await self.head(key))

        async def chunks():
            for offset in range(0, len(data), 65536):
                yield data[offset : offset + 65536]

        return chunks()

    async def delete(self, key: str) -> None:
        await self._io(self._client.remove_object, self.bucket, content_key(key))

    async def delete_prefix(self, prefix: str) -> None:
        prefix = content_prefix(prefix)
        stop = threading.Event()
        try:
            async with asyncio.timeout(self._stream_timeout):
                await self._io(
                    self._delete_prefix, prefix, stop, time.monotonic() + self._stream_timeout
                )
        finally:
            # Cancellation cannot recall an issued SDK request. Prevent any NEXT request.
            # The I/O driver retains capacity until that actual native request finishes.
            stop.set()

    def _delete_prefix(self, prefix, stop, deadline):
        def check():
            if stop.is_set() or time.monotonic() >= deadline:
                raise AdapterError(
                    "minio",
                    "dependency_unavailable",
                    "Content cleanup was interrupted",
                    True,
                    "content_store",
                )

        check()
        items = iter(self._client.list_objects(self.bucket, prefix=prefix, recursive=True))
        while True:
            check()
            item = next(items, None)
            if item is None:
                break
            check()
            if not item.object_name.startswith(prefix):
                raise ValueError("S3 listing escaped the authorized resource prefix")
            self._client.remove_object(self.bucket, item.object_name)
        check()
        # A successful response means absence was verified, not merely that DELETEs were issued.
        remaining = next(
            iter(self._client.list_objects(self.bucket, prefix=prefix, recursive=True)), None
        )
        check()
        if remaining is not None:
            raise AdapterError(
                "minio",
                "dependency_unavailable",
                "Content cleanup is not verified",
                True,
                "content_store",
            )
