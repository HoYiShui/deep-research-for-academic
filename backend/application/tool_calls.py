"""Coordinator-owned durable tool I/O; never give this service to an agent.

PG transactions finish before model/search/MinIO calls. Successfully committed
semantic results are read from immutable storage, not reissued. The unavoidable
external-response-before-durable-result window remains explicitly uncertain.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from pydantic import JsonValue, model_validator

from application.errors import AppError
from application.ports import ToolCallRepositoryPort, UnitOfWorkPort
from application.records import ClaimedRun
from application.tool_budget import ToolBudgetRequest
from domain.content import ContentRef, ResultCachePort
from domain.ports import AdapterError, ClockPort
from domain.research.models import Failure, Hash, Nonnegative, Record
from domain.research.tool_calls import ToolCallIdentity


class ToolOutput(Record):
    content: JsonValue
    tokens_used: Nonnegative | None

    @model_validator(mode="after")
    def finite_json(self):
        json.dumps(self.content, allow_nan=False)
        return self


class CachedToolOutput(ToolOutput):
    schema_version: Literal[1]
    call_key: Hash


class ToolCallService:
    def __init__(
        self,
        claimed: ClaimedRun,
        uow: UnitOfWorkPort,
        repository: ToolCallRepositoryPort,
        cache: ResultCachePort,
        clock: ClockPort,
        *,
        elapsed_base: float,
    ):
        if (
            type(elapsed_base) not in {int, float}
            or elapsed_base < 0
            or not float("inf") > elapsed_base
        ):
            raise ValueError("Execution elapsed offset must be finite and nonnegative")
        self.claimed = ClaimedRun.model_validate_json(
            ClaimedRun.model_validate(claimed).model_dump_json()
        )
        self.uow, self.repository, self.cache, self.clock = uow, repository, cache, clock
        self._elapsed_base, self._started = elapsed_base, clock.monotonic()
        self._cleanup: set[asyncio.Task] = set()

    def elapsed_s(self):
        return self._elapsed_base + max(0, self.clock.monotonic() - self._started)

    def update_claimed(self, claimed: ClaimedRun):
        """Advance this execution's cursor without changing its authority or clock.

        A resumed lease needs a new service and a persisted elapsed offset. It
        cannot be substituted into a still-running old lease's tool callbacks.
        """
        claimed = ClaimedRun.model_validate_json(claimed.model_dump_json())
        old, new = self.claimed.run, claimed.run
        if (
            claimed.owner_id != self.claimed.owner_id
            or new.run_id != old.run_id
            or new.session_id != old.session_id
            or new.lease_owner != old.lease_owner
            or new.lease_token != old.lease_token
            or new.config_snapshot != old.config_snapshot
            or new.brief_hash != old.brief_hash
            or new.brief_version != old.brief_version
            or new.checkpoint_seq < old.checkpoint_seq
        ):
            raise AppError("invalid_state", "Tool cursor cannot change execution authority")
        self.claimed = claimed

    async def invoke(
        self,
        identity: ToolCallIdentity,
        request: ToolBudgetRequest,
        operation: Callable[[], Awaitable[ToolOutput]],
        *,
        allow_uncertain_replay=False,
    ) -> ToolOutput:
        identity = ToolCallIdentity.model_validate_json(identity.model_dump_json())
        request = ToolBudgetRequest.model_validate(request)
        async with self.uow.transaction() as tx:
            receipt = await self.repository.reserve_tool_call(
                self.claimed,
                identity,
                request,
                tx,
                elapsed_s=self.elapsed_s(),
                allow_uncertain_replay=allow_uncertain_replay,
            )
        if receipt.disposition == "uncertain":
            raise AppError(
                "tool_call_uncertain", "Previous call requires explicit read-only replay"
            )
        if receipt.disposition in {"cache", "recover"}:
            # A missing/corrupt committed object is a storage failure, never a
            # license to silently repeat a previously successful paid call.
            try:
                body = await self.cache.read(receipt.reference)
            except AdapterError as exc:
                if (
                    receipt.disposition != "recover"
                    or exc.code != "content_missing"
                    or not allow_uncertain_replay
                ):
                    raise
                async with self.uow.transaction() as tx:
                    receipt = await self.repository.reserve_tool_call(
                        self.claimed,
                        identity,
                        request,
                        tx,
                        elapsed_s=self.elapsed_s(),
                        allow_uncertain_replay=True,
                        skip_result_recovery=True,
                    )
                body = None
            if body is not None:
                return await self._read_result(identity, receipt, body)
        if receipt.disposition != "execute":
            raise AppError("invalid_state", "Tool repository returned an invalid disposition")
        output = None
        try:
            remaining = self.claimed.run.config_snapshot.limits.deadline_s - self.elapsed_s()
            timeouts = self.claimed.run.config_snapshot.timeouts_s
            timeout = (
                timeouts.sandbox
                if identity.tool == "analysis"
                else getattr(timeouts, identity.tool)
            )
            if remaining <= 0:
                raise AppError("budget_exhausted", "Execution deadline is exhausted")
            async with asyncio.timeout(min(timeout, remaining)):
                output = ToolOutput.model_validate(await operation())
            self._validate_usage(identity, output)
            if identity.tool == "llm" and output.tokens_used > request.token_reservation:
                raise AppError(
                    "budget_reservation_exceeded", "Provider usage exceeded its token reservation"
                )
            envelope = CachedToolOutput(
                schema_version=1,
                call_key=identity.call_key,
                content=output.content,
                tokens_used=output.tokens_used,
            )
            body = json.dumps(
                envelope.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
            digest = sha256(body).hexdigest()
            reference = ContentRef(
                key=f"tool-results/{identity.run_id}/{digest}",
                sha256=digest,
                size=len(body),
                media_type="application/json",
            )
            async with self.uow.transaction() as tx:
                receipt = await self.repository.stage_tool_result(
                    self.claimed,
                    receipt,
                    reference,
                    output.tokens_used or 0,
                    tx,
                )
            written = await self.cache.put(
                f"tool-results/{identity.run_id}", body, "application/json"
            )
            if written != reference:
                raise AppError(
                    "invalid_state", "Object store returned a different content reference"
                )
            async with self.uow.transaction() as tx:
                await self.repository.finish_tool_call(
                    self.claimed,
                    receipt,
                    tx,
                    reference=reference,
                    tokens_used=output.tokens_used or 0,
                )
            return output
        except asyncio.CancelledError:
            await self._finish_failure(receipt, identity, output, cancelled=True)
            raise
        except Exception as exc:
            await self._finish_failure(receipt, identity, output, cancelled=False)
            if isinstance(exc, (AppError, AdapterError)):
                raise
            raise AdapterError(
                identity.tool, "dependency_unavailable", "Tool request failed", True, "tool_call"
            ) from None

    async def _read_result(self, identity, receipt, body):
        try:
            value = CachedToolOutput.model_validate_json(body)
            if value.call_key != identity.call_key:
                raise ValueError("Cached call identity differs")
            self._validate_usage(identity, value)
            if (
                receipt.disposition == "recover"
                and (value.tokens_used or 0) != receipt.staged_tokens
            ):
                raise ValueError("Cached usage differs from staged result")
        except (ValueError, TypeError):
            raise AdapterError(
                "minio", "content_invalid", "Cached tool result is invalid", False, "tool_cache"
            ) from None
        if receipt.disposition == "recover":
            async with self.uow.transaction() as tx:
                await self.repository.finish_tool_call(
                    self.claimed,
                    receipt,
                    tx,
                    reference=receipt.reference,
                    tokens_used=value.tokens_used or 0,
                )
        return ToolOutput(content=value.content, tokens_used=value.tokens_used)

    @staticmethod
    def _validate_usage(identity, output):
        if identity.tool == "llm" and output.tokens_used is None:
            raise AdapterError(
                "llm", "model_usage_invalid", "Model call has no measured usage", False, "tool_call"
            )
        if identity.tool != "llm" and output.tokens_used not in {None, 0}:
            raise ValueError("Non-model calls cannot spend model tokens")

    async def _finish_failure(self, receipt, identity, output, *, cancelled):
        failure = Failure(
            code="tool_call_interrupted" if cancelled else "tool_call_failed",
            dependency=identity.tool,
            operation="tool_call",
            phase=self.claimed.run.phase,
            message="Tool attempt interrupted" if cancelled else "Tool attempt failed",
            retryable=True,
            resume_allowed=True,
            attempt=receipt.attempt,
            occurred_at=datetime.now(UTC),
            details=None,
        )

        # Keep a strong reference and observe any late error even if the caller
        # is cancelled again. Old leases cannot persist; the next owner marks
        # that still-reserved attempt uncertain instead.
        async def persist():
            try:
                async with self.uow.transaction() as tx:
                    await self.repository.finish_tool_call(
                        self.claimed,
                        receipt,
                        tx,
                        failure=failure,
                        tokens_used=output.tokens_used if output is not None else None,
                    )
            except (AppError, AdapterError):
                return  # No fake terminal record if PG/fence is unavailable.

        task = asyncio.create_task(persist())
        self._cleanup.add(task)

        def finished(value):
            self._cleanup.discard(value)
            if not value.cancelled():
                value.exception()

        task.add_done_callback(finished)
        await asyncio.shield(task)

    async def close(self):
        if self._cleanup:
            await asyncio.gather(*self._cleanup, return_exceptions=True)
