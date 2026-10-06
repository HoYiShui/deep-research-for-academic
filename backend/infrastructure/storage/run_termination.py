"""Atomic lifecycle transitions, always locking the parent Session first."""

from pydantic import ValidationError

from application.errors import AppError
from domain.ports import AdapterError
from domain.research.models import Failure, ResearchRun, RunConfig, SessionState


class RunTermination:
    async def _owned_pair(self, owner, session_id, tx):
        from infrastructure.storage.research_postgres import RUN_JSON, decode

        conn = self.store.connection(tx)
        session = await self.get_session(owner, session_id, tx, for_update=True)
        if session is None:
            raise AppError("session_not_found", "Session not found")
        run = None
        if session.run_id is not None:
            row = await conn.fetchrow(
                "SELECT * FROM research_runs WHERE run_id=$1 AND session_id=$2 FOR UPDATE",
                session.run_id,
                session_id,
            )
            run = decode(ResearchRun, row, RUN_JSON)
            if run is None or run.status != session.status:
                raise AppError("invalid_state", "Run and Session status are inconsistent")
        return conn, session, run

    async def _terminal(self, conn, session, run, status, failure=None, *, claimed=None):
        from infrastructure.storage.research_postgres import RUN_JSON, SESSION_JSON, decode

        payload = failure.model_dump_json() if failure else None
        row = (
            await conn.fetchrow(
                "UPDATE research_runs SET status=$2,failure=$3::jsonb,resume_allowed=$4, "
                "lease_owner=NULL,lease_expires_at=NULL,finished_at=clock_timestamp() "
                "WHERE run_id=$1 AND ($5::text IS NULL OR "
                "(lease_owner=$5 AND lease_token=$6 AND lease_expires_at>clock_timestamp())) "
                "RETURNING *",
                run.run_id,
                status,
                payload,
                bool(failure and failure.resume_allowed),
                claimed.run.lease_owner if claimed else None,
                claimed.run.lease_token if claimed else None,
            )
            if run
            else None
        )
        if run is not None and row is None:
            raise AppError("stale_resource", "Run lease expired before terminal commit")
        updated = await conn.fetchrow(
            "UPDATE sessions SET status=$2,failure=$3::jsonb,revision=revision+1, "
            "updated_at=clock_timestamp() WHERE session_id=$1 RETURNING *",
            session.session_id,
            status,
            payload,
        )
        return decode(SessionState, updated, SESSION_JSON), decode(ResearchRun, row, RUN_JSON)

    async def request_cancel(self, owner, session_id, tx):
        from infrastructure.storage.research_postgres import SESSION_JSON, decode

        conn, session, run = await self._owned_pair(owner, session_id, tx)
        if session.status == "completed":
            raise AppError("invalid_session_state", "Completed research cannot be cancelled")
        if session.status in {"cancelled", "cancelling"}:
            return session
        if run is not None:
            await conn.execute(
                "UPDATE research_runs SET cancel_requested_at=coalesce(cancel_requested_at, "
                "clock_timestamp()),resume_allowed=false WHERE run_id=$1",
                run.run_id,
            )
        if session.status in {"ask", "confirm", "failed"}:
            return (await self._terminal(conn, session, run, "cancelled"))[0]
        if session.status not in {"ready", "running"} or run is None:
            raise AppError("invalid_session_state", "Research cannot be cancelled")
        await conn.execute(
            "UPDATE research_runs SET status='cancelling' WHERE run_id=$1", run.run_id
        )
        row = await conn.fetchrow(
            "UPDATE sessions SET status='cancelling',revision=revision+1, "
            "updated_at=clock_timestamp() WHERE session_id=$1 RETURNING *",
            session_id,
        )
        return decode(SessionState, row, SESSION_JSON)

    async def finish_cancelled(self, claimed, tx):
        conn, session, run = await self._locked_lease(claimed, tx)
        if run.status != "cancelling" or run.cancel_requested_at is None:
            raise AppError("invalid_session_state", "Cancellation was not requested")
        return (await self._terminal(conn, session, run, "cancelled", claimed=claimed))[1]

    @staticmethod
    def _failure_input(run, failure):
        failure = Failure.model_validate(failure)
        if run.status != "running" or run.cancel_requested_at is not None:
            raise AppError("invalid_session_state", "Cancellation takes precedence over failure")
        if failure.attempt != run.attempt_count or failure.phase != run.phase:
            raise AppError("invalid_state", "Failure must describe the current attempt and phase")
        return failure

    async def fail_run(self, claimed, failure, tx):
        conn, session, run = await self._locked_lease(claimed, tx)
        failure = self._failure_input(run, failure)
        point = await self.load_latest_checkpoint(claimed.owner_id, run.run_id, tx)
        if point is None:
            raise AppError("invalid_state", "Failure requires the last safe checkpoint")
        return (await self._terminal(conn, session, run, "failed", failure, claimed=claimed))[1]

    async def _ready_capacity(self, owner, tx, *, queue_limit=20):
        self._positive(queue_limit)
        conn = self.store.connection(tx)
        # Only freeze/resume increase this owner's queue; serialize them on User.
        # Do not take the claim advisory lock while already holding Session.
        # NO KEY UPDATE also permits FK KEY SHARE locks from newly-created CLI
        # sessions, avoiding two concurrent first freezes upgrading those locks.
        await conn.fetchval("SELECT user_id FROM users WHERE user_id=$1 FOR NO KEY UPDATE", owner)
        count = await conn.fetchval(
            "SELECT count(*) FROM research_runs r JOIN sessions s USING(session_id) "
            "WHERE s.owner_id=$1 AND r.status='ready'",
            owner,
        )
        if count >= queue_limit:
            raise AppError("rate_limited", "Research queue is full", retryable=True)

    @staticmethod
    def _resume_input(session, run, seq, config, point):
        if type(seq) is not int or seq <= 0:
            raise ValueError("Checkpoint sequence must be positive")
        config = RunConfig.model_validate(config)
        if (
            run is None
            or session.status != "failed"
            or run.status != "failed"
            or not run.resume_allowed
            or run.cancel_requested_at is not None
        ):
            raise AppError("resume_not_allowed", "Research cannot be resumed")
        if run.checkpoint_seq != seq:
            raise AppError("stale_resource", "Checkpoint sequence changed")
        if (
            point is None
            or point.seq != seq
            or point.run_id != run.run_id
            or point.phase != run.phase
            or point.state.brief_hash != run.brief_hash
            or point.state.brief_version != run.brief_version
            or point.state.session_id != session.session_id
            or point.state.source_selection != session.source_selection
            or point.state.run_metadata.config != run.config_snapshot
            or config != run.config_snapshot
        ):
            raise AppError(
                "resume_not_allowed", "Checkpoint or execution configuration is incompatible"
            )

    async def resume_run(self, owner, session_id, seq, config, tx, *, queue_limit=20):
        from infrastructure.storage.research_postgres import RUN_JSON, decode

        conn, session, run = await self._owned_pair(owner, session_id, tx)
        point = await self.load_latest_checkpoint(owner, run.run_id, tx) if run else None
        self._resume_input(session, run, seq, config, point)
        await self._ready_capacity(owner, tx, queue_limit=queue_limit)
        row = await conn.fetchrow(
            "UPDATE research_runs SET status='ready',failure=NULL,resume_allowed=false, "
            "finished_at=NULL,lease_owner=NULL,lease_expires_at=NULL WHERE run_id=$1 RETURNING *",
            run.run_id,
        )
        await conn.execute(
            "UPDATE sessions SET status='ready',failure=NULL,revision=revision+1, "
            "updated_at=clock_timestamp() WHERE session_id=$1",
            session_id,
        )
        return decode(ResearchRun, row, RUN_JSON)

    async def scan_interrupted(
        self, tx, *, queue_timeout_s=1800, limit=100, owner=None, run_id=None
    ):
        self._positive(queue_timeout_s)
        self._positive(limit)
        if run_id is not None and owner is None:
            raise ValueError("A targeted CLI scan requires an owner")
        conn = self.store.connection(tx)
        # Small batches, shared parent-first lock order; no I/O or automatic restart.
        parents = await conn.fetch(
            "SELECT s.owner_id,s.session_id FROM sessions s JOIN research_runs r USING(session_id) "
            "WHERE ($3::uuid IS NULL OR s.owner_id=$3) AND ($4::uuid IS NULL OR r.run_id=$4) "
            "AND ((r.status IN ('running','cancelling') AND "
            "(r.lease_expires_at IS NULL OR r.lease_expires_at<=clock_timestamp())) "
            "OR (r.status='ready' AND s.updated_at<=clock_timestamp()-make_interval(secs=>$1))) "
            "ORDER BY s.updated_at,s.session_id LIMIT $2 FOR UPDATE OF s SKIP LOCKED",
            queue_timeout_s,
            limit,
            owner,
            run_id,
        )
        changed = []
        for parent in parents:
            _, session, run = await self._owned_pair(parent["owner_id"], parent["session_id"], tx)
            now = await conn.fetchval("SELECT clock_timestamp()")
            # Recheck joined Run after acquiring locks: it may have been renewed
            # or claimed since the scan statement's initial snapshot.
            if run.status in {"running", "cancelling"}:
                if run.lease_expires_at is not None and run.lease_expires_at > now:
                    continue
            elif (
                run.status != "ready"
                or (now - session.updated_at).total_seconds() < queue_timeout_s
            ):
                continue
            if run.status == "cancelling":
                changed.append((await self._terminal(conn, session, run, "cancelled"))[1])
                continue
            resumable = False
            try:
                point = await self.load_latest_checkpoint(parent["owner_id"], run.run_id, tx)
                resumable = bool(
                    point
                    and point.phase == run.phase
                    and point.state.brief_hash == run.brief_hash
                    and point.state.brief_version == run.brief_version
                    and point.state.session_id == session.session_id
                    and point.state.source_selection == session.source_selection
                    and point.state.run_metadata.config == run.config_snapshot
                )
            except AdapterError as exc:
                if exc.code != "schema_incompatible":
                    raise
            except ValidationError:
                pass
            code = "interrupted" if run.status == "running" else "queue_timeout"
            failure = Failure(
                code=code if resumable else "schema_incompatible",
                dependency=None,
                operation="recovery_scan",
                phase=run.phase,
                message="Execution lease expired"
                if code == "interrupted"
                else "Queue waiting limit exceeded",
                retryable=False,
                resume_allowed=resumable,
                attempt=run.attempt_count,
                occurred_at=now,
                details=None,
            )
            changed.append((await self._terminal(conn, session, run, "failed", failure))[1])
        return changed
