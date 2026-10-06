"""Ephemeral per-subscriber broadcast plus durable, read-only PG projection."""

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from application.errors import AppError
from domain.ports import AdapterError
from domain.research.run_events import DoneFrame, ErrorFrame, PhaseFrame, RunFrame

_frames = TypeAdapter(RunFrame)


def wire_format(value):
    value = _frames.validate_python(value)
    payload = value.model_dump(mode="json", exclude={"event_id", "event"})
    return f"id: {value.event_id}\nevent: {value.event}\ndata: {json.dumps(payload, ensure_ascii=False, allow_nan=False)}\n\n"


@dataclass(frozen=True)
class QueuedEvent:
    event_id: UUID
    run_id: UUID
    checkpoint_seq: int
    event: str
    wire: str
    status: str | None
    fatal: bool


@dataclass(eq=False)
class Subscription:
    session_id: UUID
    queue: asyncio.Queue
    closed: bool = False


class RunEventBus:
    def __init__(self, *, queue_size=256):
        if type(queue_size) is not int or queue_size <= 0:
            raise ValueError("Subscription capacity must be positive")
        self.queue_size = queue_size
        self._subscribers = {}

    @property
    def subscriber_count(self):
        return sum(len(items) for items in self._subscribers.values())

    def subscribe(self, session_id):
        subscription = Subscription(UUID(str(session_id)), asyncio.Queue(self.queue_size))
        self._subscribers.setdefault(subscription.session_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription):
        subscription.closed = True
        subscriptions = self._subscribers.get(subscription.session_id)
        if subscriptions is not None:
            subscriptions.discard(subscription)
            if not subscriptions:
                del self._subscribers[subscription.session_id]

    def emit(self, value):
        value = _frames.validate_python(value)
        # Queue immutable serialized wire text, not caller-owned mutable dictionaries.
        item = QueuedEvent(
            value.event_id,
            value.run_id,
            value.checkpoint_seq,
            value.event,
            wire_format(value),
            getattr(value, "status", None),
            getattr(value, "fatal", False),
        )
        for subscription in tuple(self._subscribers.get(value.session_id, ())):
            try:
                subscription.queue.put_nowait(item)
            except asyncio.QueueFull:
                self.unsubscribe(subscription)


class _Stream:
    def __init__(self, iterator, bus, subscription):
        self.iterator, self.bus, self.subscription = iterator, bus, subscription

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await anext(self.iterator)

    async def aclose(self):
        self.bus.unsubscribe(self.subscription)
        await self.iterator.aclose()


class RunEventStream:
    def __init__(self, queries, bus, *, poll_s=5, heartbeat_s=15):
        if any(
            isinstance(value, bool) or not math.isfinite(value) or value <= 0
            for value in (poll_s, heartbeat_s)
        ):
            raise ValueError("Stream intervals must be finite and positive")
        self.queries, self.bus = queries, bus
        self.poll_s, self.heartbeat_s = poll_s, heartbeat_s

    @staticmethod
    def _common(view):
        return {
            "event_id": uuid4(),
            "session_id": view["session_id"],
            "run_id": view["run_id"],
            "timestamp": datetime.now(UTC),
            "checkpoint_seq": view["checkpoint_seq"],
        }

    @classmethod
    def _projection(cls, view):
        fields = cls._common(view)
        if view["status"] in {"completed", "failed", "cancelled"}:
            return DoneFrame(
                **fields,
                event="done",
                status=view["status"],
                review_verdict=view["review_verdict"],
                failure=view["failure"],
                final_report_url=f"/research/{view['session_id']}/report"
                if view["status"] == "completed"
                else None,
            )
        return PhaseFrame(
            **fields,
            event="phase",
            phase=view["phase"],
            status=view["status"],
            message="Current committed run state",
        )

    async def open(self, owner, session_id, *, expires_at=None):
        subscription = self.bus.subscribe(session_id)
        try:
            view = await self.queries.session_view(owner, session_id)
            if view["run_id"] is None:
                raise AppError("invalid_session_state", "Events require a frozen brief")
            if expires_at is not None and expires_at <= datetime.now(UTC):
                raise AppError("unauthenticated", "Authentication expired")
            # Validate before HTTP sends headers, including corrupted terminal views.
            initial = self._projection(view)
            return _Stream(
                self._stream(owner, view, initial, subscription, expires_at), self.bus, subscription
            )
        except BaseException:
            self.bus.unsubscribe(subscription)
            raise

    async def _stream(self, owner, view, initial, subscription, expires_at):
        loop = asyncio.get_running_loop()
        next_poll, next_heartbeat = loop.time() + self.poll_s, loop.time() + self.heartbeat_s
        seen = set()
        try:
            yield wire_format(initial)
            if initial.event == "done":
                return
            while True:
                if expires_at is not None and datetime.now(UTC) >= expires_at:
                    return
                if subscription.closed:
                    yield wire_format(
                        ErrorFrame(
                            **self._common(view),
                            event="error",
                            code="slow_consumer",
                            message="Stream queue exceeded capacity; read current Session state",
                            recoverable=True,
                            fatal=True,
                        )
                    )
                    return
                now = loop.time()
                if now >= next_poll:
                    try:
                        current = await self.queries.session_view(owner, UUID(view["session_id"]))
                    except (AdapterError, AppError):
                        yield wire_format(
                            ErrorFrame(
                                **self._common(view),
                                event="error",
                                code="dependency_unavailable",
                                message="Current persisted state is temporarily unavailable",
                                recoverable=True,
                                fatal=True,
                            )
                        )
                        return
                    if (
                        current["run_id"] != view["run_id"]
                        or current["checkpoint_seq"] < view["checkpoint_seq"]
                    ):
                        yield wire_format(
                            ErrorFrame(
                                **self._common(view),
                                event="error",
                                code="invalid_state",
                                message="Run identity or checkpoint changed unexpectedly",
                                recoverable=False,
                                fatal=True,
                            )
                        )
                        return
                    if (current["checkpoint_seq"], current["status"]) != (
                        view["checkpoint_seq"],
                        view["status"],
                    ):
                        view = current
                        event = self._projection(view)
                        yield wire_format(event)
                        if event.event == "done":
                            return
                    next_poll = loop.time() + self.poll_s
                if now >= next_heartbeat:
                    yield ": heartbeat\n\n"
                    next_heartbeat = loop.time() + self.heartbeat_s
                delay = min(next_poll, next_heartbeat) - loop.time()
                if expires_at is not None:
                    delay = min(delay, (expires_at - datetime.now(UTC)).total_seconds())
                try:
                    queued = await asyncio.wait_for(
                        subscription.queue.get(), timeout=max(0.001, delay)
                    )
                except TimeoutError:
                    continue
                if (
                    queued.run_id != UUID(view["run_id"])
                    or queued.checkpoint_seq < view["checkpoint_seq"]
                    or queued.event_id in seen
                ):
                    continue
                if queued.event in {"phase", "done"}:
                    # An event queued before bootstrap may describe an older
                    # same-seq status. Validate transitions against PG, not arrival order.
                    try:
                        current = await self.queries.session_view(owner, UUID(view["session_id"]))
                    except (AdapterError, AppError):
                        yield wire_format(
                            ErrorFrame(
                                **self._common(view),
                                event="error",
                                code="dependency_unavailable",
                                message="Current persisted state is temporarily unavailable",
                                recoverable=True,
                                fatal=True,
                            )
                        )
                        return
                    if (
                        current["run_id"] != view["run_id"]
                        or current["checkpoint_seq"] < view["checkpoint_seq"]
                    ):
                        yield wire_format(
                            ErrorFrame(
                                **self._common(view),
                                event="error",
                                code="invalid_state",
                                message="Run identity or checkpoint changed unexpectedly",
                                recoverable=False,
                                fatal=True,
                            )
                        )
                        return
                    if (
                        queued.checkpoint_seq != current["checkpoint_seq"]
                        or queued.status != current["status"]
                    ):
                        if (current["checkpoint_seq"], current["status"]) != (
                            view["checkpoint_seq"],
                            view["status"],
                        ):
                            view = current
                            event = self._projection(view)
                            yield wire_format(event)
                            if event.event == "done":
                                return
                        continue
                    view = current
                seen.add(queued.event_id)
                # Bound local de-duplication; IDs are not a persistent replay log.
                if len(seen) > 512:
                    seen = {queued.event_id}
                yield queued.wire
                if queued.event == "done" or queued.fatal:
                    return
        finally:
            self.bus.unsubscribe(subscription)
