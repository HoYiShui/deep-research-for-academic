"""Failure-only metadata diagnostics for invocation-owned integration databases.

No queries/briefs/checkpoint state, content, config, object keys, credentials,
arbitrary Failure text, or subprocess stderr are included. These observations
must not replace test gates, lengthen deadlines, or claim a successful Run.
"""

import asyncio
import json
import re
from contextlib import asynccontextmanager


async def snapshot(pool):
    try:
        async with asyncio.timeout(2):
            database = await pool.fetchval("SELECT current_database()")
            if not re.fullmatch(r"dr4a_test_[a-f0-9]{32}", database):
                return {"unavailable": "not_an_isolated_test_database"}
            sessions = await pool.fetch(
                "SELECT session_id,status,revision,run_id FROM sessions ORDER BY created_at LIMIT 20"
            )
            runs = await pool.fetch(
                "SELECT run_id,status,phase,checkpoint_seq,attempt_count,lease_token,"
                "lease_expires_at,clock_timestamp()>lease_expires_at AS lease_expired,"
                "created_at,started_at,finished_at FROM research_runs ORDER BY created_at LIMIT 20"
            )
            checkpoints = await pool.fetch(
                "SELECT run_id,seq,phase,created_at FROM phase_snapshots ORDER BY run_id,seq LIMIT 100"
            )
            attempts = await pool.fetch(
                "SELECT tool,status,count(*) AS count,min(created_at) AS first_created_at,"
                "max(updated_at) AS last_updated_at FROM tool_call_attempts GROUP BY tool,status"
            )
            activity = await pool.fetch(
                "SELECT state,wait_event_type,wait_event,count(*) AS count FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                "GROUP BY state,wait_event_type,wait_event"
            )
            return {
                "sessions": [dict(row) for row in sessions],
                "runs": [dict(row) for row in runs],
                "checkpoints": [dict(row) for row in checkpoints],
                "attempts": [dict(row) for row in attempts],
                "activity": [dict(row) for row in activity],
            }
    except Exception as exc:  # noqa: BLE001 -- diagnostics cannot mask the original failure.
        return {"unavailable": type(exc).__name__}


@asynccontextmanager
async def diagnose_on_failure(pool):
    try:
        yield
    except Exception:
        print(
            "run_failure_metadata=" + json.dumps(await snapshot(pool), default=str, sort_keys=True)
        )
        raise
