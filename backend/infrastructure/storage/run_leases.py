"""Short PG lease transactions; no model I/O, local clock or in-memory capacity."""

from uuid import UUID

from pydantic import TypeAdapter

from application.errors import AppError
from application.records import ClaimedRun
from domain.research.models import ResearchRun, Text
from domain.research.state import Checkpoint

RUN_CAPACITY_LOCK = 0x44523441


class RunLeases:
    """ResearchRepository lease extension; Session locks precede Run locks."""

    @staticmethod
    def _positive(value):
        if type(value) is not int or value <= 0:
            raise ValueError("Lease and capacity settings must be positive integers")

    async def claim_run(
        self,
        worker: str,
        tx,
        *,
        owner: UUID | None = None,
        run_id: UUID | None = None,
        lease_s=90,
        global_limit=2,
        owner_limit=1,
    ) -> ClaimedRun | None:
        from infrastructure.storage.research_postgres import RUN_JSON, decode

        worker = TypeAdapter(Text).validate_python(worker)
        for value in (lease_s, global_limit, owner_limit):
            self._positive(value)
        if run_id is not None and owner is None:
            raise ValueError("A targeted CLI claim requires an owner")
        conn = self.store.connection(tx)
        # Only claims can increase active capacity. The lock covers both checks
        # and the transition across all servers/CLI processes using this database.
        await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", RUN_CAPACITY_LOCK)
        count = await conn.fetchval(
            "SELECT count(*) FROM research_runs WHERE status IN ('running','cancelling') "
            "AND lease_expires_at > clock_timestamp()"
        )
        if count >= global_limit:
            return None
        session = await conn.fetchrow(
            "SELECT s.session_id,s.owner_id FROM sessions s JOIN research_runs r USING(session_id) "
            "WHERE s.status='ready' AND r.status='ready' AND r.cancel_requested_at IS NULL "
            "AND ($1::uuid IS NULL OR s.owner_id=$1) AND ($2::uuid IS NULL OR r.run_id=$2) "
            "AND (SELECT count(*) FROM research_runs a JOIN sessions p USING(session_id) "
            "WHERE p.owner_id=s.owner_id AND a.status IN ('running','cancelling') "
            "AND a.lease_expires_at>clock_timestamp()) < $3 "
            "ORDER BY r.created_at,r.run_id LIMIT 1 FOR UPDATE OF s SKIP LOCKED",
            owner,
            run_id,
            owner_limit,
        )
        if session is None:
            return None
        row = await conn.fetchrow(
            "UPDATE research_runs SET status='running',attempt_count=attempt_count+1, "
            "lease_owner=$2,lease_token=lease_token+1, "
            "lease_expires_at=clock_timestamp()+make_interval(secs=>$3), "
            "started_at=coalesce(started_at,clock_timestamp()),finished_at=NULL, "
            "failure=NULL,resume_allowed=false WHERE session_id=$1 AND status='ready' "
            "AND cancel_requested_at IS NULL RETURNING *",
            session["session_id"],
            worker,
            lease_s,
        )
        if row is None:
            raise AppError("stale_resource", "Run changed before claim")
        await conn.execute(
            "UPDATE sessions SET status='running',revision=revision+1,failure=NULL, "
            "updated_at=clock_timestamp() WHERE session_id=$1",
            session["session_id"],
        )
        return ClaimedRun(owner_id=session["owner_id"], run=decode(ResearchRun, row, RUN_JSON))

    async def renew_lease(self, claimed: ClaimedRun, tx, *, lease_s=90) -> ClaimedRun:
        from infrastructure.storage.research_postgres import RUN_JSON, decode

        claimed = ClaimedRun.model_validate(claimed)
        self._positive(lease_s)
        conn = self.store.connection(tx)
        session = await self.get_session(
            claimed.owner_id, claimed.run.session_id, tx, for_update=True
        )
        if session is None:
            raise AppError("session_not_found", "Session not found")
        row = await conn.fetchrow(
            "UPDATE research_runs SET lease_expires_at=clock_timestamp()+make_interval(secs=>$5) "
            "WHERE run_id=$1 AND session_id=$2 AND lease_owner=$3 AND lease_token=$4 "
            "AND status IN ('running','cancelling') AND lease_expires_at>clock_timestamp() RETURNING *",
            claimed.run.run_id,
            session.session_id,
            claimed.run.lease_owner,
            claimed.run.lease_token,
            lease_s,
        )
        if row is None:
            raise AppError("stale_resource", "Run lease is no longer owned")
        return ClaimedRun(owner_id=claimed.owner_id, run=decode(ResearchRun, row, RUN_JSON))

    async def load_latest_checkpoint(self, owner: UUID, run_id: UUID, tx=None):
        from infrastructure.storage.research_postgres import decode

        async with self.store.read(tx) as conn:
            row = await conn.fetchrow(
                "SELECT p.* FROM phase_snapshots p JOIN research_runs r "
                "ON r.run_id=p.run_id AND r.checkpoint_seq=p.seq "
                "JOIN sessions s ON s.session_id=r.session_id "
                "WHERE s.owner_id=$1 AND r.run_id=$2",
                owner,
                run_id,
            )
            return decode(Checkpoint, row, {"state"})

    async def _locked_lease(self, claimed, tx):
        from infrastructure.storage.research_postgres import RUN_JSON, decode

        claimed = ClaimedRun.model_validate(claimed)
        conn = self.store.connection(tx)
        session = await self.get_session(
            claimed.owner_id, claimed.run.session_id, tx, for_update=True
        )
        if session is None:
            raise AppError("session_not_found", "Session not found")
        row = await conn.fetchrow(
            "SELECT * FROM research_runs WHERE run_id=$1 AND session_id=$2 "
            "AND lease_owner=$3 AND lease_token=$4 AND lease_expires_at>clock_timestamp() "
            "AND status IN ('running','cancelling') FOR UPDATE",
            claimed.run.run_id,
            session.session_id,
            claimed.run.lease_owner,
            claimed.run.lease_token,
        )
        if row is None:
            raise AppError("stale_resource", "Run lease is no longer owned")
        run = decode(ResearchRun, row, RUN_JSON)
        if session.run_id != run.run_id or session.status != run.status:
            raise AppError("invalid_state", "Run and Session status are inconsistent")
        return conn, session, run

    @staticmethod
    def _checkpoint_input(run, session, previous, point, expected_seq):
        if type(expected_seq) is not int or expected_seq <= 0:
            raise ValueError("Expected checkpoint sequence must be a positive integer")
        if run.checkpoint_seq != expected_seq or point.seq != expected_seq + 1:
            raise AppError("stale_resource", "Checkpoint sequence changed")
        state = point.state
        if (
            point.run_id != run.run_id
            or state.session_id != session.session_id
            or state.brief_hash != run.brief_hash
            or state.brief_version != run.brief_version
            or state.run_metadata.config != run.config_snapshot
            or state.source_selection != session.source_selection
            or state.phase == "done"
            or state.final_report is not None
        ):
            raise AppError(
                "invalid_state", "Checkpoint changes frozen input or requires report publication"
            )
        if previous is None:
            raise AppError("invalid_state", "Current checkpoint is unavailable")
        before, after = previous.state.run_metadata, state.run_metadata
        if (
            any(
                getattr(after.budget_used, name) < getattr(before.budget_used, name)
                for name in type(before.budget_used).model_fields
            )
            or after.rework_count < before.rework_count
        ):
            raise AppError("invalid_state", "Checkpoint cannot reset consumed execution budget")

    async def commit_checkpoint(self, claimed, expected_seq, checkpoint, tx):
        from infrastructure.storage.research_postgres import RUN_JSON, decode, encode, insert

        checkpoint = Checkpoint.model_validate(checkpoint)
        conn, session, run = await self._locked_lease(claimed, tx)
        previous = await self.load_latest_checkpoint(claimed.owner_id, run.run_id, tx)
        self._checkpoint_input(run, session, previous, checkpoint, expected_seq)
        await insert(
            conn,
            "phase_snapshots",
            encode(checkpoint, {"state"}) | {"session_id": session.session_id},
        )
        row = await conn.fetchrow(
            "UPDATE research_runs SET checkpoint_seq=$5,phase=$6 WHERE run_id=$1 "
            "AND lease_owner=$2 AND lease_token=$3 AND checkpoint_seq=$4 "
            "AND lease_expires_at>clock_timestamp() AND status IN ('running','cancelling') RETURNING *",
            run.run_id,
            claimed.run.lease_owner,
            claimed.run.lease_token,
            expected_seq,
            checkpoint.seq,
            checkpoint.phase,
        )
        if row is None:
            raise AppError("stale_resource", "Checkpoint lease or sequence changed")
        await conn.execute(
            "UPDATE sessions SET revision=revision+1,updated_at=clock_timestamp() WHERE session_id=$1",
            session.session_id,
        )
        return ClaimedRun(owner_id=claimed.owner_id, run=decode(ResearchRun, row, RUN_JSON))
