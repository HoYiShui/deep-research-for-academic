"""Fenced PG tool reservations: short transactions, no SDK or MinIO I/O."""

from application.errors import AppError
from application.tool_budget import ToolBudgetRequest, check_tool_budget
from application.tool_records import ToolBudgetView, ToolReservation
from domain.content import ContentRef
from domain.research.ids import canonical_hash
from domain.research.models import Failure
from domain.research.state import BudgetUsage
from domain.research.tool_calls import ToolCallIdentity, ToolCallRecord


class RunToolCalls:
    async def claim_run(self, worker, tx, **options):
        claimed = await super().claim_run(worker, tx, **options)
        if claimed is None:
            return None
        conn = self.store.connection(tx)
        # Reclassify every unfinished old-lease attempt, not merely whichever
        # semantic call the resumed coordinator happens to invoke first.
        await conn.execute(
            "UPDATE tool_call_attempts a SET status='uncertain',updated_at=clock_timestamp() "
            "FROM tool_calls c WHERE c.call_id=a.call_id AND c.run_id=$1 "
            "AND a.status='reserved' AND a.lease_token<$2",
            claimed.run.run_id,
            claimed.run.lease_token,
        )
        await conn.execute(
            "UPDATE tool_calls c SET status='uncertain',updated_at=clock_timestamp() "
            "WHERE c.run_id=$1 AND c.status='reserved' AND EXISTS(SELECT 1 FROM tool_call_attempts a "
            "WHERE a.call_id=c.call_id AND a.status='uncertain' AND a.lease_token<$2)",
            claimed.run.run_id,
            claimed.run.lease_token,
        )
        await self._tool_fence(conn, claimed)
        return claimed

    async def _check_tool_snapshot(self, claimed, state, tx, *, publication=False):
        conn, _session, run = await self._locked_lease(claimed, tx)
        budget = await self._tool_budget(run.run_id, conn)
        if budget is None:
            return
        value = state.run_metadata.budget_used
        if any(
            getattr(value, field) != getattr(budget.used, field)
            for field in ("llm_calls", "search_calls", "fetch_calls", "tokens")
        ) or (value.elapsed_s < budget.used.elapsed_s):
            raise AppError("invalid_state", "Checkpoint differs from durable tool accounting")
        if publication and await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM tool_call_attempts a JOIN tool_calls c USING(call_id) "
            "WHERE c.run_id=$1 AND a.status='reserved')",
            run.run_id,
        ):
            raise AppError("invalid_state", "Cannot publish with outstanding tool reservations")

    async def commit_checkpoint(self, claimed, expected_seq, checkpoint, tx):
        from domain.research.state import Checkpoint

        checkpoint = Checkpoint.model_validate(checkpoint)
        await self._check_tool_snapshot(claimed, checkpoint.state, tx)
        return await super().commit_checkpoint(claimed, expected_seq, checkpoint, tx)

    async def publish_report(self, claimed, expected_seq, checkpoint, tx):
        from domain.research.state import Checkpoint

        checkpoint = Checkpoint.model_validate(checkpoint)
        await self._check_tool_snapshot(claimed, checkpoint.state, tx, publication=True)
        return await super().publish_report(claimed, expected_seq, checkpoint, tx)

    async def _tool_baseline(self, claimed, run, conn, tx, elapsed_s):
        point = await self.load_latest_checkpoint(claimed.owner_id, run.run_id, tx)
        if point is None:
            raise AppError("invalid_state", "Tool budget needs a committed checkpoint")
        elapsed = BudgetUsage.model_validate(
            point.state.run_metadata.budget_used.model_dump() | {"elapsed_s": elapsed_s}
        ).elapsed_s
        await conn.execute(
            "INSERT INTO tool_budget_baselines(run_id,checkpoint_seq,usage,elapsed_s) "
            "VALUES($1,$2,$3,$4) ON CONFLICT(run_id) DO NOTHING",
            run.run_id,
            point.seq,
            point.state.run_metadata.budget_used.model_dump_json(),
            max(elapsed, point.state.run_metadata.budget_used.elapsed_s),
        )
        await conn.execute(
            "UPDATE tool_budget_baselines SET elapsed_s=greatest(elapsed_s,$2) WHERE run_id=$1",
            run.run_id,
            max(elapsed, point.state.run_metadata.budget_used.elapsed_s),
        )
        return point

    async def _tool_budget(self, run_id, conn):
        row = await conn.fetchrow("SELECT * FROM tool_budget_baselines WHERE run_id=$1", run_id)
        if row is None:
            return None
        base = BudgetUsage.model_validate_json(row["usage"])
        used = base.model_dump() | {"elapsed_s": row["elapsed_s"]}
        pending = {"llm_calls": 0, "search_calls": 0, "fetch_calls": 0, "tokens": 0, "elapsed_s": 0}
        counts = await conn.fetch(
            "SELECT a.tool,a.status,count(*) AS calls,"
            "coalesce(sum(coalesce(a.tokens_used,a.tokens_reserved)),0) AS tokens "
            "FROM tool_call_attempts a JOIN tool_calls c USING(call_id) "
            "WHERE c.run_id=$1 GROUP BY a.tool,a.status",
            run_id,
        )
        for item in counts:
            target = pending if item["status"] == "reserved" else used
            if item["tool"] != "analysis":
                target[item["tool"] + "_calls"] += item["calls"]
            target["tokens"] += int(item["tokens"])
        return ToolBudgetView(
            used=BudgetUsage.model_validate(used), pending=BudgetUsage.model_validate(pending)
        )

    @staticmethod
    async def _tool_fence(conn, claimed):
        valid = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM research_runs WHERE run_id=$1 AND lease_owner=$2 "
            "AND lease_token=$3 AND lease_expires_at>clock_timestamp() "
            "AND status IN ('running','cancelling'))",
            claimed.run.run_id,
            claimed.run.lease_owner,
            claimed.run.lease_token,
        )
        if not valid:
            raise AppError("stale_resource", "Tool reservation lease is no longer owned")

    async def _tool_receipt(self, row, attempt, disposition, conn):
        from infrastructure.storage.research_postgres import decode

        record = decode(ToolCallRecord, row, {"failure"})
        reference = None
        if record.status == "succeeded":
            if row["result_size"] is None or row["result_media_type"] is None:
                raise AppError("invalid_state", "Cached call lacks a verified content reference")
            reference = ContentRef(
                key=record.result_object_key,
                sha256=record.result_hash,
                size=row["result_size"],
                media_type=row["result_media_type"],
            )
        return ToolReservation(
            disposition=disposition,
            record=record,
            attempt=attempt["attempt"],
            lease_token=attempt["lease_token"],
            uncertain_replay=attempt["uncertain_replay"],
            reference=reference,
            budget=await self._tool_budget(record.run_id, conn),
        )

    async def reserve_tool_call(
        self, claimed, identity, request, tx, *, elapsed_s=0, allow_uncertain_replay=False
    ):
        identity = ToolCallIdentity.model_validate(identity)
        request = ToolBudgetRequest.model_validate(request)
        if type(allow_uncertain_replay) is not bool:
            raise ValueError("Replay permission must be an explicit boolean")
        conn, _session, run = await self._locked_lease(claimed, tx)
        if run.status != "running" or run.cancel_requested_at is not None:
            raise AppError("invalid_session_state", "Execution is stopping")
        if (
            identity.run_id != run.run_id
            or identity.source_policy != run.config_snapshot.source_policy
        ):
            raise AppError("invalid_state", "Tool call differs from frozen execution scope")
        if identity.tool != request.tool:
            raise AppError("invalid_state", "Tool and budget request differ")
        if (
            identity.tool == "llm"
            and identity.source_policy.private_only
            and identity.provider != "local"
        ):
            raise AppError(
                "privacy_policy_conflict", "Private scope cannot invoke an external model"
            )
        point = await self._tool_baseline(claimed, run, conn, tx, elapsed_s)
        if {canonical_hash(v) for v in identity.knowledge_snapshot} != {
            canonical_hash(v) for v in point.state.run_metadata.knowledge_snapshot
        }:
            raise AppError("invalid_state", "Tool call knowledge snapshot is not frozen")
        row = await conn.fetchrow(
            "SELECT * FROM tool_calls WHERE run_id=$1 AND call_key=$2",
            run.run_id,
            identity.call_key,
        )
        previous = None
        if row is not None:
            if row["request_hash"] != identity.call_key or row["call_id"] != identity.call_id:
                raise AppError("invalid_state", "Tool call identity conflicts with persisted data")
            previous = await conn.fetchrow(
                "SELECT * FROM tool_call_attempts WHERE call_id=$1 ORDER BY attempt DESC LIMIT 1",
                identity.call_id,
            )
            if previous is None:
                raise AppError("invalid_state", "Tool call has no recoverable attempt ledger")
            if row["status"] == "succeeded":
                await self._tool_fence(conn, claimed)
                return await self._tool_receipt(row, previous, "cache", conn)
            if row["status"] == "reserved":
                if previous["lease_token"] == run.lease_token:
                    raise AppError("tool_call_in_progress", "Tool call is already reserved")
                await conn.execute(
                    "UPDATE tool_call_attempts SET status='uncertain',updated_at=clock_timestamp() "
                    "WHERE call_id=$1 AND attempt=$2 AND status='reserved'",
                    identity.call_id,
                    previous["attempt"],
                )
                row = await conn.fetchrow(
                    "UPDATE tool_calls SET status='uncertain',updated_at=clock_timestamp() "
                    "WHERE call_id=$1 RETURNING *",
                    identity.call_id,
                )
            if row["status"] == "uncertain" and not allow_uncertain_replay:
                await self._tool_fence(conn, claimed)
                return await self._tool_receipt(row, previous, "uncertain", conn)
        budget = await self._tool_budget(run.run_id, conn)
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM tool_call_attempts a JOIN tool_calls c USING(call_id) "
            "WHERE c.run_id=$1 AND a.tokens_used>a.tokens_reserved)",
            run.run_id,
        ):
            raise AppError("budget_reservation_exceeded", "Provider exceeded its token reservation")
        check_tool_budget(run.config_snapshot, budget.used, budget.pending, request)
        replay = row is not None and row["status"] == "uncertain"
        if replay and identity.tool not in {"llm", "search", "fetch"}:
            raise AppError("invalid_state", "Only read-only calls can replay an uncertain attempt")
        if row is None:
            row = await conn.fetchrow(
                "INSERT INTO tool_calls(call_id,run_id,call_key,status,request_hash,budget_units,"
                "created_at,updated_at) VALUES($1,$2,$3,'reserved',$3,1,clock_timestamp(),"
                "clock_timestamp()) RETURNING *",
                identity.call_id,
                run.run_id,
                identity.call_key,
            )
        else:
            row = await conn.fetchrow(
                "UPDATE tool_calls SET status='reserved',failure=NULL,budget_units=budget_units+1,"
                "updated_at=clock_timestamp() WHERE call_id=$1 RETURNING *",
                identity.call_id,
            )
        attempt = await conn.fetchrow(
            "INSERT INTO tool_call_attempts(call_id,attempt,lease_token,tool,status,tokens_reserved,"
            "uncertain_replay) VALUES($1,$2,$3,$4,'reserved',$5,$6) RETURNING *",
            identity.call_id,
            previous["attempt"] + 1 if previous else 1,
            run.lease_token,
            identity.tool,
            request.token_reservation,
            replay,
        )
        await self._tool_fence(conn, claimed)
        return await self._tool_receipt(row, attempt, "execute", conn)

    async def finish_tool_call(
        self, claimed, reservation, tx, *, reference=None, tokens_used=None, failure=None
    ):
        reservation = ToolReservation.model_validate(reservation)
        if (
            reservation.disposition != "execute"
            or reservation.lease_token != claimed.run.lease_token
        ):
            raise AppError("invalid_state", "Tool result requires its active reservation")
        reference = ContentRef.model_validate(reference) if reference is not None else None
        failure = Failure.model_validate(failure) if failure is not None else None
        if (reference is None) == (failure is None):
            raise ValueError("Provide a successful result or a failure, not both")
        if tokens_used is not None and (type(tokens_used) is not int or tokens_used < 0):
            raise ValueError("Measured token usage must be a nonnegative integer")
        conn, _session, run = await self._locked_lease(claimed, tx)
        if reservation.record.run_id != run.run_id:
            raise AppError("invalid_state", "Reservation belongs to another Run")
        if reference is not None and not reference.key.startswith(f"tool-results/{run.run_id}/"):
            raise AppError("invalid_state", "Result object is outside its Run namespace")
        current = await conn.fetchrow(
            "SELECT * FROM tool_call_attempts WHERE call_id=$1 AND attempt=$2 "
            "AND lease_token=$3 AND status='reserved'",
            reservation.record.call_id,
            reservation.attempt,
            run.lease_token,
        )
        if current is None:
            raise AppError("stale_resource", "Tool reservation changed")
        if current["tool"] == "llm" and reference is not None and tokens_used is None:
            raise ValueError("Successful model calls require measured usage")
        if current["tool"] != "llm" and tokens_used not in {None, 0}:
            raise ValueError("Non-model calls cannot spend model tokens")
        status = (
            "succeeded"
            if reference is not None
            else "uncertain"
            if tokens_used is None
            else "failed"
        )
        failure_json = failure.model_dump_json() if failure is not None else None
        attempt = await conn.fetchrow(
            "UPDATE tool_call_attempts SET status=$4,tokens_used=$5,failure=$6,"
            "updated_at=clock_timestamp() WHERE call_id=$1 AND attempt=$2 AND lease_token=$3 "
            "RETURNING *",
            reservation.record.call_id,
            reservation.attempt,
            run.lease_token,
            status,
            tokens_used,
            failure_json,
        )
        row = await conn.fetchrow(
            "UPDATE tool_calls SET status=$2,result_object_key=$3,result_hash=$4,"
            "result_size=$5,result_media_type=$6,failure=$7,updated_at=clock_timestamp() "
            "WHERE call_id=$1 RETURNING *",
            reservation.record.call_id,
            status,
            reference.key if reference else None,
            reference.sha256 if reference else None,
            reference.size if reference else None,
            reference.media_type if reference else None,
            failure_json,
        )
        await self._tool_fence(conn, claimed)
        return await self._tool_receipt(row, attempt, "finished", conn)

    async def load_tool_budget(self, owner, run_id, tx=None):
        async with self.store.read(tx) as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM research_runs r JOIN sessions s USING(session_id) "
                "WHERE s.owner_id=$1 AND r.run_id=$2)",
                owner,
                run_id,
            )
            if not exists:
                raise AppError("session_not_found", "Session not found")
            return await self._tool_budget(run_id, conn)
