"""Owner-scoped, consistent projections of committed research state."""

from uuid import UUID

from application.errors import AppError
from application.ports import ResearchRepositoryPort, UnitOfWorkPort
from domain.ports import AdapterError
from domain.research.models import ResearchBrief


class ResearchQueries:
    def __init__(self, uow: UnitOfWorkPort, research: ResearchRepositoryPort):
        self.uow, self.research = uow, research

    async def session_view(self, owner: UUID, session_id: UUID) -> dict:
        # A committed MVCC snapshot preserves Session/Run/Checkpoint consistency
        # without making a ready Session disappear from SKIP LOCKED claims.
        async with self.uow.snapshot() as tx:
            session = await self.research.get_session(owner, session_id, tx)
            if session is None:
                raise AppError("session_not_found", "Session not found")
            run = await self.research.get_run(owner, session.run_id, tx) if session.run_id else None
            checkpoint = None
            if session.run_id:
                if (
                    run is None
                    or run.session_id != session_id
                    or run.status != session.status
                    or run.brief_version != session.brief_version
                    or run.failure != session.failure
                ):
                    self._inconsistent()
                checkpoint = await self.research.load_checkpoint(
                    owner, run.run_id, run.checkpoint_seq, tx
                )
                if (
                    checkpoint is None
                    or checkpoint.phase != run.phase
                    or checkpoint.state.session_id != session_id
                    or checkpoint.state.brief_version != run.brief_version
                    or checkpoint.state.brief_hash != run.brief_hash
                    or checkpoint.state.source_selection != session.source_selection
                    or checkpoint.state.run_metadata.config != run.config_snapshot
                ):
                    self._inconsistent()
                if run.status == "completed" and (
                    run.phase != "done"
                    or checkpoint.state.review_verdict is None
                    or checkpoint.state.final_report is None
                ):
                    self._inconsistent()
            view = {
                "session_id": str(session_id),
                "status": session.status,
                "phase": run.phase if run else None,
                "revision": session.revision,
                "brief_version": session.brief_version,
                "clarification_round": session.clarification_round,
                "clarification_limit_reached": session.clarification_limit_reached,
                "source_selection": session.source_selection.model_dump(mode="json"),
                "run_id": str(run.run_id) if run else None,
                "checkpoint_seq": run.checkpoint_seq if run else None,
                "resume_allowed": run.resume_allowed if run else False,
                "failure": session.failure.model_dump(mode="json") if session.failure else None,
                "review_verdict": checkpoint.state.review_verdict if checkpoint else None,
                "sse_url": f"/research/{session_id}/events" if run else None,
            }
            if session.status == "ask":
                view.update(
                    questions=session.pending_questions,
                    missing_fields=session.missing_fields,
                    brief_draft=session.brief_draft.model_dump(mode="json"),
                )
            elif session.status == "confirm":
                view["research_brief"] = ResearchBrief.model_validate(
                    session.brief_draft.model_dump()
                ).model_dump(mode="json")
            elif run:
                brief = await self.research.load_brief(owner, session_id, session.brief_version, tx)
                if brief is None or brief.frozen_at is None or brief.content_hash != run.brief_hash:
                    self._inconsistent()
                view["research_brief"] = ResearchBrief.model_validate(
                    brief.content.model_dump()
                ).model_dump(mode="json")
            return view

    @staticmethod
    def _inconsistent():
        raise AdapterError(
            "postgres",
            "schema_incompatible",
            "Persisted projection is inconsistent",
            False,
            "session_view",
        )

    async def checkpoint_view(self, owner: UUID, session_id: UUID) -> dict:
        """Trusted CLI projection of current seq, including a reworked phase."""
        async with self.uow.snapshot() as tx:
            session = await self.research.get_session(owner, session_id, tx)
            if session is None:
                raise AppError("session_not_found", "Session not found")
            if session.run_id is None:
                raise AppError("checkpoint_not_found", "Session has no Run checkpoint")
            run = await self.research.get_run(owner, session.run_id, tx)
            if run is None or run.session_id != session_id or run.status != session.status:
                self._inconsistent()
            point = await self.research.load_checkpoint(owner, run.run_id, run.checkpoint_seq, tx)
            if (
                point is None
                or point.seq != run.checkpoint_seq
                or point.run_id != run.run_id
                or point.state.run_id != run.run_id
                or point.phase != run.phase
                or point.state.session_id != session_id
                or point.state.brief_version != run.brief_version
                or point.state.brief_hash != run.brief_hash
                or point.state.source_selection != session.source_selection
                or point.state.run_metadata.config != run.config_snapshot
            ):
                self._inconsistent()
            return {
                "session_id": str(session_id),
                "run_id": str(run.run_id),
                "checkpoint_seq": point.seq,
                "phase": point.phase,
                "state": point.state.model_dump(mode="json"),
            }

    async def report_view(self, owner: UUID, session_id: UUID) -> dict:
        async with self.uow.snapshot() as tx:
            session = await self.research.get_session(owner, session_id, tx)
            if session is None:
                raise AppError("session_not_found", "Session not found")
            if session.status != "completed" or session.run_id is None:
                raise AppError("report_not_ready", "Report has not been published")
            run = await self.research.get_run(owner, session.run_id, tx)
            report = await self.research.load_report(owner, session.run_id, tx)
            point = await self.research.load_latest_checkpoint(owner, session.run_id, tx)
            if (
                run is None
                or report is None
                or point is None
                or run.status != "completed"
                or run.phase != "done"
                or point.phase != "done"
                or point.state.final_report != report
            ):
                self._inconsistent()
            return {
                "session_id": str(session_id),
                "report_id": str(report.report_id),
                "version": report.version,
                "review_verdict": report.review_verdict,
                "report": report.markdown,
                "references": [item.model_dump(mode="json") for item in report.references],
                "risks": [item.model_dump(mode="json") for item in report.risks],
            }
