"""Production report assembly and four-fact PG publisher; no model calls."""

from uuid import uuid4

from application.errors import AppError
from domain.research.ids import canonical_hash
from domain.research.reporting import build_report
from domain.research.state import Checkpoint, PipelineState


def serialize_report(state, *, report_id, created_at):
    try:
        return build_report(state, report_id=report_id, created_at=created_at)
    except (ValueError, TypeError, KeyError):
        raise AppError(
            "invalid_state", "Reviewed draft cannot pass report delivery checks"
        ) from None


class ReportPublisher:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock

    async def publish(self, claimed, point):
        report = serialize_report(point.state, report_id=uuid4(), created_at=self.clock.now_utc())
        state = PipelineState.model_validate(
            point.state.model_dump() | {"phase": "done", "final_report": report}
        )
        candidate = Checkpoint(
            snapshot_id=uuid4(),
            run_id=point.run_id,
            seq=point.seq + 1,
            schema_version=1,
            phase="done",
            state=state,
            state_hash=canonical_hash(state),
            created_at=self.clock.now_utc(),
        )
        async with self.store.transaction() as tx:
            await self.store.research.publish_report(claimed, point.seq, candidate, tx)
