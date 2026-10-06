"""Real MinIO result cache: bounded I/O, immutable hash keys, verified reads.

No bucket is silently created. Deployment owns the bucket and permissions.
The same address can only be written with identical bytes, including concurrent
writers. Arbitrary mutable object names are intentionally not accepted.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import urllib3
from minio import Minio
from minio.error import S3Error

from domain.content import ContentRef
from domain.ports import AdapterError

MAX_RESULT_BYTES = 10 * 1024 * 1024
_NAMESPACE = re.compile(r"^[a-z][a-z0-9_-]{0,63}/[a-zA-Z0-9_-]{1,128}$")
_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}/[a-zA-Z0-9_-]{1,128}/[a-f0-9]{64}$")


class MinioObjectIO:
    """Shared bounded SDK transport and byte-integrity mechanics, not a public Port."""

    max_bytes = MAX_RESULT_BYTES
    operation_name = "content_cache"

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        *,
        secure: bool = False,
        timeout_s: float = 30,
    ) -> None:
        if not bucket or timeout_s <= 0:
            raise ValueError("Bucket and positive timeout are required")
        self.bucket = bucket
        self._http = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=min(timeout_s, 5), read=timeout_s),
            retries=False,
            maxsize=2,
            block=True,
        )
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            http_client=self._http,
        )
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="minio-cache")
        self._slots = asyncio.Semaphore(2)
        self._inflight: set[asyncio.Future] = set()
        self._closed = False

    async def _io(self, function, *args):
        if self._closed:
            raise RuntimeError("Content cache is closed")
        # Keep ownership until the actual thread finishes, even if its awaiting
        # caller is cancelled repeatedly. Cancellation cannot stop an S3 request.
        await self._slots.acquire()
        if self._closed:
            self._slots.release()
            raise RuntimeError("Content cache is closed")
        future = asyncio.get_running_loop().run_in_executor(
            self._executor,
            partial(function, *args),
        )
        self._inflight.add(future)

        def finished(value):
            self._inflight.discard(value)
            self._slots.release()
            if not value.cancelled():
                value.exception()  # Observe errors even after caller cancellation.

        future.add_done_callback(finished)
        try:
            return await asyncio.shield(future)
        except AdapterError:
            raise
        except S3Error as exc:
            missing = exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}
            raise AdapterError(
                "minio",
                "content_missing" if missing else "dependency_unavailable",
                "Stored content is unavailable",
                not missing,
                self.operation_name,
            ) from None
        except Exception:  # noqa: BLE001 -- sanitize all foreign SDK failures.
            raise AdapterError(
                "minio",
                "dependency_unavailable",
                "Content storage request failed",
                True,
                self.operation_name,
            ) from None

    def _put(self, reference: ContentRef, content: bytes) -> None:
        try:
            # Verify bytes, not an S3 multipart ETag or user-controlled metadata.
            existing = self._read(reference)
        except S3Error as exc:
            if exc.code not in {"NoSuchKey", "NoSuchObject"}:
                raise
        else:
            if existing != content:
                raise self._corrupt()
            return
        self._client.put_object(
            self.bucket,
            reference.key,
            io.BytesIO(content),
            len(content),
            content_type=reference.media_type,
            num_parallel_uploads=1,
        )
        self._read(reference)

    def _read(self, reference: ContentRef) -> bytes:
        response = self._client.get_object(self.bucket, reference.key)
        try:
            content = response.read(self.max_bytes + 1)
        finally:
            response.close()
            response.release_conn()
        if (
            len(content) != reference.size
            or hashlib.sha256(content).hexdigest() != reference.sha256
        ):
            raise self._corrupt()
        return content

    def _corrupt(self) -> AdapterError:
        return AdapterError(
            "minio",
            "content_hash_mismatch",
            "Stored content failed integrity validation",
            False,
            self.operation_name,
        )

    async def close(self) -> None:
        self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
        self._http.clear()


class MinioResultCache(MinioObjectIO):
    """10MiB tool/unit results with exact two-part immutable namespaces."""

    async def put(self, namespace: str, content: bytes, media_type: str) -> ContentRef:
        if not _NAMESPACE.fullmatch(namespace):
            raise ValueError("A two-part cache namespace is required")
        if not isinstance(content, bytes) or len(content) > self.max_bytes:
            raise ValueError("Result must be bytes within the 10 MiB limit")
        if not media_type or any(c in media_type for c in "\r\n"):
            raise ValueError("A valid media type is required")
        digest = hashlib.sha256(content).hexdigest()
        reference = ContentRef(
            key=f"{namespace}/{digest}",
            sha256=digest,
            size=len(content),
            media_type=media_type,
        )
        await self._io(self._put, reference, content)
        return reference

    async def read(self, reference: ContentRef) -> bytes:
        reference = ContentRef.model_validate(reference)
        if (
            not _KEY.fullmatch(reference.key)
            or reference.key.rsplit("/", 1)[-1] != reference.sha256
            or reference.size > self.max_bytes
        ):
            raise ValueError("Invalid content-addressed reference")
        return await self._io(self._read, reference)
