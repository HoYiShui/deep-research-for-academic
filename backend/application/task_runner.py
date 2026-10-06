"""One local worker; PostgreSQL is the cross-process ownership/capacity truth.

An executor is mandatory and explicitly supplied by the composition root.
This module does not fabricate phase results or silently fall back to a fake.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from application.errors import AppError
from application.records import ClaimedRun
from application.settings import Settings
from domain.ports import AdapterError
from domain.research.models import Failure

Executor = Callable[[ClaimedRun, asyncio.Event], Awaitable[None]]
logger = logging.getLogger(__name__)


class TaskRunner:
    def __init__(self, *, store, execute: Executor, settings: Settings, worker_id=None):
        if not callable(execute):
            raise TypeError("A real or explicitly controlled executor is required")
        if settings.heartbeat_s >= settings.lease_s:
            raise ValueError("Heartbeat interval must be shorter than the execution lease")
        self.store, self.execute, self.settings = store, execute, settings
        self.worker_id = worker_id or str(uuid4())
        self._stop, self._wake = asyncio.Event(), asyncio.Event()
        self._loop_task = None
        self._active = None
        self.closed = False
        self._tick_lock = asyncio.Lock()

    @property
    def active(self):
        return self._active if self._active and not self._active.done() else None

    def wake(self):
        self._wake.set()

    async def start(self):
        if self.closed:
            raise RuntimeError("Runner is closed")
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop(), name="dr4a-run-scanner")

    async def _loop(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 -- observe safely; a DB outage is not done.failed
                logger.warning("run_scan_failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.settings.scan_s)
            except TimeoutError:
                pass

    async def tick(self):
        async with self._tick_lock:
            if self._stop.is_set() or self.closed:
                return
            async with self.store.transaction() as tx:
                await self.store.research.scan_interrupted(
                    tx, queue_timeout_s=self.settings.queue_timeout_s
                )
            if self.active is not None:
                return
            async with self.store.transaction() as tx:
                claimed = await self.store.research.claim_run(
                    self.worker_id,
                    tx,
                    lease_s=self.settings.lease_s,
                    global_limit=self.settings.pipeline_concurrency,
                    owner_limit=self.settings.owner_run_concurrency,
                )
            if claimed is not None:
                self._active = asyncio.create_task(
                    self._run(claimed), name=f"dr4a-run-{claimed.run.run_id}"
                )
                self._active.add_done_callback(self._observed)

    def _observed(self, task):
        if not task.cancelled() and task.exception() is not None:
            logger.warning("run_task_failed")
        if self._active is task:
            self._active = None
        self.wake()

    async def _heartbeat(self, claimed):
        while True:
            await asyncio.sleep(self.settings.heartbeat_s)
            async with self.store.transaction() as tx:
                await self.store.research.renew_lease(claimed, tx, lease_s=self.settings.lease_s)

    async def _record_failure(self, claimed, code, *, resumable, dependency=None):
        try:
            async with asyncio.timeout(self.settings.shutdown_s):
                async with self.store.transaction() as tx:
                    current = await self.store.research.get_run(
                        claimed.owner_id, claimed.run.run_id, tx
                    )
                    if current is None or current.status not in {"running", "cancelling"}:
                        return
                    if current.status == "cancelling":
                        await self.store.research.finish_cancelled(claimed, tx)
                    else:
                        failure = Failure(
                            code=code,
                            dependency=dependency,
                            operation="execute_run",
                            phase=current.phase,
                            message="Run execution stopped before publication",
                            retryable=False,
                            resume_allowed=resumable,
                            attempt=current.attempt_count,
                            occurred_at=datetime.now(UTC),
                            details=None,
                        )
                        await self.store.research.fail_run(claimed, failure, tx)
        except AppError as exc:
            if exc.code != "stale_resource":
                logger.warning("run_terminal_not_committed")
        except Exception:  # noqa: BLE001 -- PG may be offline; scanner recovers persisted lease later
            logger.warning("run_terminal_not_committed")

    async def _run(self, claimed):
        work = asyncio.create_task(self.execute(claimed, self._stop), name="dr4a-run-executor")
        heartbeat = asyncio.create_task(self._heartbeat(claimed), name="dr4a-run-heartbeat")
        try:
            done, _ = await asyncio.wait((work, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                await work
                # A coroutine returning isn't persisted completion. The executor
                # must have committed its terminal transaction before returning.
                await self._record_failure(claimed, "invalid_execution_result", resumable=False)
            else:
                await heartbeat  # Lost lease / DB outage interrupts in-flight work.
        except asyncio.CancelledError:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            await self._record_failure(claimed, "interrupted", resumable=True)
            raise
        except AppError as exc:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            if exc.code != "stale_resource":
                await self._record_failure(claimed, "execution_failed", resumable=False)
        except AdapterError as exc:
            # Stop external I/O before declaring the task stopped.
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            await self._record_failure(
                claimed,
                "dependency_unavailable",
                resumable=exc.retryable,
                dependency=exc.dependency,
            )
        except Exception:  # noqa: BLE001 -- never expose provider args/trace/credentials in Failure
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            await self._record_failure(claimed, "execution_failed", resumable=False)
        finally:
            for task in (work, heartbeat):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        self._stop.set()
        self.wake()
        if self._loop_task:
            try:
                await asyncio.wait_for(self._loop_task, timeout=self.settings.shutdown_s)
            except TimeoutError:
                self._loop_task.cancel()
                await asyncio.gather(self._loop_task, return_exceptions=True)
        active = self._active
        if active:
            try:
                await asyncio.wait_for(asyncio.shield(active), timeout=self.settings.shutdown_s)
            except TimeoutError:
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
            finally:
                self._active = None
