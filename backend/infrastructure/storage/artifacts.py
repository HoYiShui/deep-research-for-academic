"""Private, immutable, bounded analysis attachments; no public S3 URLs."""

import hashlib
import re
from uuid import UUID

from domain.content import ContentRef
from infrastructure.storage.content_cache import MinioObjectIO

MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "csv": "text/csv",
    "json": "application/json",
    "txt": "text/plain",
}


def attachment_key(key):
    parts = key.split("/")
    if (
        len(parts) != 5
        or parts[0] != "analysis"
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", parts[2])
        or not re.fullmatch(r"[a-f0-9]{64}", parts[3])
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", parts[4])
        or parts[4].rsplit(".", 1)[-1].lower() not in MEDIA_TYPES
        or str(UUID(parts[1])) != parts[1]
    ):
        raise ValueError("Invalid immutable attachment key")
    return parts


class MinioArtifactStore(MinioObjectIO):
    operation_name = "artifact_store"

    async def put(self, key: str, body: bytes) -> None:
        parts = attachment_key(key)
        if not isinstance(body, bytes) or len(body) > self.max_bytes:
            raise ValueError("Attachment exceeds byte limit")
        if hashlib.sha256(body).hexdigest() != parts[3]:
            raise ValueError("Attachment hash differs from its key")
        reference = ContentRef(
            key=key,
            sha256=parts[3],
            size=len(body),
            media_type=MEDIA_TYPES[parts[4].rsplit(".", 1)[-1].lower()],
        )
        await self._io(self._put, reference, body)

    async def read(self, key: str) -> bytes:
        parts = attachment_key(key)
        return await self._io(self._read_attachment, key, parts[3])

    def _read_attachment(self, key, digest):
        stat = self._client.stat_object(self.bucket, key)
        if not 0 <= stat.size <= self.max_bytes:
            raise self._corrupt()
        return self._read(
            ContentRef(
                key=key,
                sha256=digest,
                size=stat.size,
                media_type=MEDIA_TYPES[key.rsplit(".", 1)[-1].lower()],
            )
        )
