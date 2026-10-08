"""Fixed Standalone index; PG owns visibility and authorization, never this SDK."""

import asyncio
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from uuid import UUID

from application.knowledge_models import VectorHit
from application.vector_models import IndexEmbedding, IndexRow, VectorScope
from domain.ports import AdapterError

COLLECTION = "dr4a_chunks_v1"
METADATA = [
    "chunk_id",
    "kb_id",
    "document_id",
    "document_version_id",
    "index_version",
    "chunk_type",
    "page_start",
    "year",
]


def partition(kb_id):
    if not isinstance(kb_id, UUID):
        raise TypeError("Knowledge base identity must be UUID")
    return "kb_" + kb_id.hex


def expression(scope):
    scope = VectorScope.model_validate(scope)
    clauses = [
        f"kb_id == {json.dumps(str(scope.kb_id))}",
        f"document_version_id in {json.dumps([str(item) for item in scope.version_ids])}",
        f"index_version == {json.dumps(scope.index_version)}",
    ]
    if scope.document_ids:
        clauses.append(f"document_id in {json.dumps([str(item) for item in scope.document_ids])}")
    if scope.chunk_types:
        clauses.append(f"chunk_type in {json.dumps(scope.chunk_types)}")
    if scope.year_min is not None:
        clauses.append(f"year >= {scope.year_min}")
    if scope.year_max is not None:
        clauses.append(f"year <= {scope.year_max}")
    return " and ".join(clauses)


class MilvusIndex:
    def __init__(self, uri, *, timeout_s=15):
        parsed = urlsplit(uri)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Standalone HTTP endpoint required; Lite is not supported")
        if type(timeout_s) is not int or not 1 <= timeout_s <= 60:
            raise ValueError("Invalid index timeout")
        self.uri, self._timeout_s = uri, timeout_s
        self.thread_context = threading.local()
        self.client = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dr4a-index")
        self.slot = asyncio.Semaphore(1)
        self.closed = False

    @property
    def timeout(self):
        stop = getattr(self.thread_context, "stop", None)
        deadline = getattr(self.thread_context, "deadline", None)
        if stop is None or deadline is None:
            return self._timeout_s
        remaining = deadline - time.monotonic()
        if stop.is_set() or remaining <= 0:
            raise TimeoutError("Index operation was stopped")
        return remaining

    def _client(self):
        if self.client is None:
            from pymilvus import MilvusClient

            self.client = MilvusClient(uri=self.uri, timeout=self.timeout)
        return self.client

    async def _call(self, operation, function):
        if self.closed:
            raise AdapterError("vector", "index_not_ready", "Index is closed", True, operation)
        deadline = time.monotonic() + self._timeout_s
        try:
            await asyncio.wait_for(self.slot.acquire(), timeout=self._timeout_s)
        except TimeoutError:
            raise AdapterError(
                "vector", "index_not_ready", "Index capacity timed out", True, operation
            ) from None
        if self.closed:
            self.slot.release()
            raise AdapterError("vector", "index_not_ready", "Index is closed", True, operation)
        loop = asyncio.get_running_loop()
        stop = threading.Event()

        def native():
            self.thread_context.stop = stop
            self.thread_context.deadline = deadline
            _ = self.timeout  # validate the remaining total operation budget
            return function()

        try:
            future = loop.run_in_executor(self.executor, native)
        except BaseException:
            self.slot.release()
            raise

        # Cancellation must not permit another SDK operation while this one is
        # still executing. The native request itself has a bounded deadline.
        def finished(result):
            self.slot.release()
            if not result.cancelled():
                result.exception()  # observe even if the caller was cancelled

        future.add_done_callback(finished)
        try:
            async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                return await asyncio.shield(future)
        except asyncio.CancelledError:
            stop.set()
            raise
        except AdapterError:
            stop.set()
            raise
        except Exception:  # noqa: BLE001 -- sanitize arbitrary SDK errors at the boundary
            stop.set()
            raise AdapterError(
                "vector", "index_not_ready", "Index operation failed", True, operation
            ) from None

    @staticmethod
    def _fields():
        from pymilvus import DataType

        return [
            ("chunk_id", DataType.VARCHAR, {"max_length": 256, "is_primary": True}),
            *[
                (name, DataType.VARCHAR, {"max_length": 36})
                for name in ["kb_id", "document_id", "document_version_id"]
            ],
            ("index_version", DataType.VARCHAR, {"max_length": 128}),
            ("chunk_type", DataType.VARCHAR, {"max_length": 16}),
            ("page_start", DataType.INT64, {"nullable": True}),
            ("year", DataType.INT64, {"nullable": True}),
            ("dense_vector", DataType.FLOAT_VECTOR, {"dim": 1024}),
            ("sparse_vector", DataType.SPARSE_FLOAT_VECTOR, {}),
        ]

    def _schema(self):
        client = self._client()
        if not client.has_collection(COLLECTION, timeout=self.timeout):
            schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
            for name, dtype, options in self._fields():
                schema.add_field(name, datatype=dtype, **options)
            indexes = client.prepare_index_params()
            indexes.add_index("dense_vector", index_type="FLAT", metric_type="COSINE")
            indexes.add_index("sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
            client.create_collection(
                COLLECTION,
                schema=schema,
                index_params=indexes,
                consistency_level="Strong",
                timeout=self.timeout,
            )
        description = client.describe_collection(COLLECTION, timeout=self.timeout)
        fields = {field["name"]: field for field in description["fields"]}
        if (
            description.get("auto_id")
            or description.get("enable_dynamic_field")
            or set(fields) != {item[0] for item in self._fields()}
        ):
            raise ValueError("Incompatible index schema")
        for name, dtype, options in self._fields():
            actual = fields[name]
            if (
                actual["type"] != dtype
                or bool(actual.get("is_primary", False)) != bool(options.get("is_primary", False))
                or bool(actual.get("nullable", False)) != bool(options.get("nullable", False))
            ):
                raise ValueError("Incompatible field")
            for parameter in ["dim", "max_length"]:
                if (
                    parameter in options
                    and int(actual.get("params", {}).get(parameter, 0)) != options[parameter]
                ):
                    raise ValueError("Incompatible field parameter")
        for name, metric, algorithm in [
            ("dense_vector", "COSINE", "FLAT"),
            ("sparse_vector", "IP", "SPARSE_INVERTED_INDEX"),
        ]:
            actual = client.describe_index(COLLECTION, name, timeout=self.timeout)
            if actual.get("metric_type") != metric or actual.get("index_type") != algorithm:
                raise ValueError("Incompatible vector index")

    async def ensure_schema(self, index_version: str) -> None:
        from pydantic import TypeAdapter

        from application.vector_models import Profile

        TypeAdapter(Profile).validate_python(index_version)
        await self._call("ensure_schema", self._schema)

    async def ensure_partition(self, kb_id: UUID) -> None:
        name = partition(kb_id)

        def ensure():
            client = self._client()
            if not client.has_partition(COLLECTION, name, timeout=self.timeout):
                client.create_partition(COLLECTION, name, timeout=self.timeout)
            if not client.has_partition(COLLECTION, name, timeout=self.timeout):
                raise ValueError("Partition was not created")
            client.load_partitions(COLLECTION, [name], timeout=self.timeout)

        await self._call("ensure_partition", ensure)

    @staticmethod
    def _rows(rows):
        if not 1 <= len(rows) <= 128:
            raise ValueError("Index batches require 1..128 rows")
        values = [IndexRow.model_validate(row) for row in rows]
        if (
            len({row.chunk_id for row in values}) != len(values)
            or len({row.kb_id for row in values}) != 1
        ):
            raise ValueError("Batch identity is not unique and scoped")
        return values

    async def upsert(self, rows: list[IndexRow]) -> None:
        rows = self._rows(rows)
        data = [
            row.model_dump()
            | {
                name: str(getattr(row, name))
                for name in ["kb_id", "document_id", "document_version_id"]
            }
            for row in rows
        ]
        await self._call(
            "upsert",
            lambda: self._client().upsert(
                COLLECTION, data, partition_name=partition(rows[0].kb_id), timeout=self.timeout
            ),
        )

    async def verify_rows(self, rows: list[IndexRow]) -> bool:
        rows = self._rows(rows)

        def verify():
            actual = self._client().get(
                COLLECTION,
                [row.chunk_id for row in rows],
                partition_names=[partition(rows[0].kb_id)],
                output_fields=METADATA + ["dense_vector", "sparse_vector"],
                consistency_level="Strong",
                timeout=self.timeout,
            )
            by_id = {row["chunk_id"]: row for row in actual}
            if len(by_id) != len(actual) or set(by_id) != {row.chunk_id for row in rows}:
                raise ValueError("Index rows missing or unexpected")
            for expected in rows:
                value = by_id[expected.chunk_id]
                for field in METADATA:
                    reference = getattr(expected, field)
                    if isinstance(reference, UUID):
                        reference = str(reference)
                    if value.get(field) != reference:
                        raise ValueError("Index mapping differs")
                dense, sparse = value["dense_vector"], value["sparse_vector"]
                if len(dense) != 1024 or set(sparse) != set(expected.sparse_vector):
                    raise ValueError("Index vector shape differs")
                for first, second in list(zip(dense, expected.dense_vector, strict=True)) + [
                    (sparse[token], weight) for token, weight in expected.sparse_vector.items()
                ]:
                    if not math.isclose(first, second, rel_tol=1e-6, abs_tol=1e-7):
                        raise ValueError("Index vector differs")
            return True

        return await self._call("verify_rows", verify)

    async def hybrid_search(
        self, embedding: IndexEmbedding, scope: VectorScope, candidate_k: int = 20
    ) -> list[VectorHit]:
        embedding, scope = (
            IndexEmbedding.model_validate(embedding),
            VectorScope.model_validate(scope),
        )
        if type(candidate_k) is not int or not 1 <= candidate_k <= 20:
            raise ValueError("Candidate limit must be 1..20")

        def search():
            client = self._client()
            ranks = []
            for field, vector, metric in [
                ("dense_vector", embedding.dense_vector, "COSINE"),
                ("sparse_vector", embedding.sparse_vector, "IP"),
            ]:
                response = client.search(
                    COLLECTION,
                    data=[vector],
                    anns_field=field,
                    filter=expression(scope),
                    partition_names=[partition(scope.kb_id)],
                    limit=candidate_k,
                    search_params={"metric_type": metric, "params": {}},
                    output_fields=METADATA,
                    consistency_level="Strong",
                    timeout=self.timeout,
                )
                if len(response) != 1:
                    raise ValueError("Unexpected index query shape")
                route = {}
                for rank, hit in enumerate(response[0], 1):
                    value = hit["entity"]
                    if (
                        value["kb_id"] != str(scope.kb_id)
                        or value["document_version_id"]
                        not in {str(item) for item in scope.version_ids}
                        or value["index_version"] != scope.index_version
                        or (
                            scope.document_ids
                            and value["document_id"]
                            not in {str(item) for item in scope.document_ids}
                        )
                        or (scope.chunk_types and value["chunk_type"] not in scope.chunk_types)
                        or (
                            scope.year_min is not None
                            and (value["year"] is None or value["year"] < scope.year_min)
                        )
                        or (
                            scope.year_max is not None
                            and (value["year"] is None or value["year"] > scope.year_max)
                        )
                    ):
                        raise ValueError("Index returned an out-of-scope row")
                    identity = hit["chunk_id"]
                    if (
                        identity in route
                        or identity != value["chunk_id"]
                        or not math.isfinite(hit["distance"])
                    ):
                        raise ValueError("Invalid index hit")
                    route[identity] = rank
                ranks.append(route)
            combined = [
                VectorHit(
                    chunk_id=identity,
                    dense_rank=ranks[0].get(identity),
                    sparse_rank=ranks[1].get(identity),
                    fused_score=sum(
                        1 / (60 + route[identity]) for route in ranks if identity in route
                    ),
                )
                for identity in ranks[0].keys() | ranks[1].keys()
            ]
            return sorted(combined, key=lambda hit: (-hit.fused_score, hit.chunk_id))[:candidate_k]

        return await self._call("hybrid_search", search)

    async def delete_version(self, kb_id: UUID, version_id: UUID) -> None:
        if not isinstance(version_id, UUID):
            raise TypeError("Version identity must be UUID")
        name = partition(kb_id)
        query = f"kb_id == {json.dumps(str(kb_id))} and document_version_id == {json.dumps(str(version_id))}"

        def delete():
            client = self._client()
            # Cleanup may resume after the KB partition was already removed.
            # A missing scope is success, but dependency errors still propagate.
            if not client.has_collection(
                COLLECTION, timeout=self.timeout
            ) or not client.has_partition(COLLECTION, name, timeout=self.timeout):
                return
            client.delete(COLLECTION, filter=query, partition_name=name, timeout=self.timeout)
            if client.query(
                COLLECTION,
                filter=query,
                partition_names=[name],
                output_fields=["chunk_id"],
                limit=1,
                consistency_level="Strong",
                timeout=self.timeout,
            ):
                raise ValueError("Deleted version is still readable")

        await self._call("delete_version", delete)

    async def drop_partition(self, kb_id: UUID) -> None:
        name = partition(kb_id)

        def drop():
            client = self._client()
            if not client.has_collection(COLLECTION, timeout=self.timeout):
                return
            if client.has_partition(COLLECTION, name, timeout=self.timeout):
                client.release_partitions(COLLECTION, [name], timeout=self.timeout)
                client.drop_partition(COLLECTION, name, timeout=self.timeout)
            if client.has_partition(COLLECTION, name, timeout=self.timeout):
                raise ValueError("Partition still exists")

        await self._call("drop_partition", drop)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)
        if self.client is not None:
            await asyncio.to_thread(self.client.close)
