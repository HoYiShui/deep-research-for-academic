"""Four-fact publication transaction. Research-quality gates live in domain code."""

import json

from application.errors import AppError
from domain.research.facts import FinalReport
from domain.research.models import ResearchRun
from domain.research.reporting import validate_publication
from domain.research.state import Checkpoint
from infrastructure.storage.run_leases import RunLeases


class RunPublication:
    @staticmethod
    def _publication_input(run, session, previous, point, expected_seq):
        if run.status != "running" or run.cancel_requested_at is not None:
            raise AppError("invalid_session_state", "Cancelled research cannot publish a report")
        RunLeases._checkpoint_input(run, session, previous, point, expected_seq, publication=True)
        before, state, report = previous.state, point.state, point.state.final_report
        section_ids = {f"section_{index}" for index in range(1, 6)}
        if (
            before.phase != "review"
            or before.reviewed_draft_version != before.draft_version
            or before.draft_version < 1
            or before.review_verdict is None
            or before.model_dump(exclude={"phase", "final_report"})
            != state.model_dump(exclude={"phase", "final_report"})
            or report.version != 1
            or set(report.sections) != section_ids
            or report.sections != state.draft_sections
            or report.bindings != state.draft_claim_bindings
            or any(
                section.draft_version != state.draft_version for section in report.sections.values()
            )
        ):
            raise AppError("invalid_state", "Publication must preserve the complete reviewed draft")
        for reference in report.references:
            if reference.source_id not in state.sources or any(
                evidence_id not in state.evidence
                or state.evidence[evidence_id].source_id != reference.source_id
                for evidence_id in reference.evidence_ids
            ):
                raise AppError(
                    "invalid_state", "Report reference does not locate committed evidence"
                )
        try:
            validate_publication(before, report)
        except (ValueError, TypeError, KeyError):
            raise AppError(
                "invalid_state", "Report violates deterministic delivery checks"
            ) from None

    async def publish_report(self, claimed, expected_seq, checkpoint, tx):
        from infrastructure.storage.research_postgres import RUN_JSON, decode, encode, insert

        point = Checkpoint.model_validate(checkpoint)
        conn, session, run = await self._locked_lease(claimed, tx)
        previous = await self.load_latest_checkpoint(claimed.owner_id, run.run_id, tx)
        self._publication_input(run, session, previous, point, expected_seq)
        report = point.state.final_report
        await insert(
            conn,
            "reports",
            {
                "report_id": report.report_id,
                "run_id": run.run_id,
                "session_id": session.session_id,
                "version": report.version,
                "content": report.model_dump_json(),
                "created_at": report.created_at,
            },
        )
        await insert(
            conn, "phase_snapshots", encode(point, {"state"}) | {"session_id": session.session_id}
        )
        row = await conn.fetchrow(
            "UPDATE research_runs SET status='completed',phase='done',checkpoint_seq=$5, "
            "finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL, "
            "failure=NULL,resume_allowed=false WHERE run_id=$1 AND lease_owner=$2 "
            "AND lease_token=$3 AND checkpoint_seq=$4 AND lease_expires_at>clock_timestamp() "
            "AND status='running' AND cancel_requested_at IS NULL RETURNING *",
            run.run_id,
            claimed.run.lease_owner,
            claimed.run.lease_token,
            expected_seq,
            point.seq,
        )
        if row is None:
            raise AppError("stale_resource", "Publication lease or sequence changed")
        await conn.execute(
            "UPDATE sessions SET status='completed',failure=NULL,revision=revision+1, "
            "updated_at=clock_timestamp() WHERE session_id=$1",
            session.session_id,
        )
        return decode(ResearchRun, row, RUN_JSON)

    async def load_report(self, owner, run_id, tx=None):
        from infrastructure.storage.research_postgres import decode

        async with self.store.read(tx) as conn:
            content = await conn.fetchval(
                "SELECT p.content FROM reports p JOIN sessions s USING(session_id) "
                "WHERE s.owner_id=$1 AND p.run_id=$2",
                owner,
                run_id,
            )
            if content is None:
                return None
            data = json.loads(content) if isinstance(content, str) else content
            return decode(FinalReport, data, set())
