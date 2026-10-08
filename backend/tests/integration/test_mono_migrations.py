"""Real PostgreSQL migration tests with synthetic legacy records only."""

import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from application.settings import Settings
from domain.research.ids import canonical_hash
from domain.research.state import PipelineState
from infrastructure.storage.migrations import run_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "infrastructure/storage/migrations"
LEGACY_TABLES = ("users", "sessions", "messages", "briefs", "reports", "phase_snapshots")


@pytest.mark.asyncio
async def test_empty_database_and_repeat_migration(pg_database):
    pool, _ = pg_database
    await run_migrations(pool)
    tables = set(await pool.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    assert {
        "users",
        "sessions",
        "messages",
        "briefs",
        "research_runs",
        "phase_snapshots",
        "reports",
        "tool_calls",
        "idempotency_requests",
    } <= {row[0] for row in tables}
    versions = await pool.fetch("SELECT version FROM schema_migrations ORDER BY version")
    assert [row[0] for row in versions] == [
        "0001_init",
        "0002_mono_research",
        "0003_tool_call_attempts",
        "0004_staged_tool_results",
        "0005_mono_knowledge",
    ]
    await run_migrations(pool)
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 5
    assert (
        await pool.fetchval(
            "SELECT data_type FROM information_schema.columns WHERE table_name='sessions' AND column_name='session_id'"
        )
        == "uuid"
    )


async def seed_legacy(pool):
    await run_migrations(pool, through_version="0001_init")
    owner = uuid4().hex
    await pool.execute(
        "INSERT INTO users(user_id,email,password_hash) VALUES($1,$2,$3)",
        owner,
        "Fixture@Example.org",
        "synthetic-bcrypt-hash",
    )
    await pool.execute(
        "INSERT INTO users(user_id,email,password_hash) VALUES('not-a-uuid','other@example.org','fixture-hash')"
    )
    await pool.execute(
        "INSERT INTO sessions(session_id,user_id,status) VALUES('legacy-owned',$1,'ready')", owner
    )
    await pool.execute("INSERT INTO sessions(session_id,status) VALUES('legacy-unowned','clarify')")
    await pool.execute(
        "INSERT INTO messages(session_id,role,content) VALUES('legacy-owned','user','Synthetic query')"
    )
    await pool.execute(
        "INSERT INTO briefs(session_id,brief) VALUES('legacy-owned','{\"claims_to_verify\":[\"synthetic\"]}')"
    )
    await pool.execute(
        "INSERT INTO reports(session_id,content) VALUES('legacy-owned','{\"title\":\"synthetic report\"}')"
    )
    await pool.execute(
        "INSERT INTO phase_snapshots(session_id,phase,state) VALUES('legacy-owned','done','{\"phase\":\"done\"}')"
    )
    return owner


async def snapshot_rows(pool, prefix=""):
    return {
        name: await pool.fetch(f'SELECT * FROM "{prefix}{name}" ORDER BY 1')
        for name in LEGACY_TABLES
    }


@pytest.mark.asyncio
async def test_legacy_rows_are_preserved_and_explicitly_isolated(pg_database, tmp_path):
    pool, _ = pg_database
    owner = await seed_legacy(pool)
    before = await snapshot_rows(pool)
    original_json = {
        name: [
            json.loads(row["row"])
            for row in await pool.fetch(f'SELECT to_jsonb(t) AS row FROM "{name}" t ORDER BY 1')
        ]
        for name in LEGACY_TABLES
    }
    await run_migrations(pool, backup_dir=tmp_path)
    assert await snapshot_rows(pool, "legacy_") == before
    backup = next(tmp_path.glob("mono-upgrade-*/legacy-backup.jsonl"))
    checksum = backup.with_suffix(".sha256").read_text().strip()
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == checksum
    assert backup.stat().st_mode & 0o777 == 0o600
    assert backup.parent.stat().st_mode & 0o777 == 0o700
    records = [json.loads(line) for line in backup.read_text().splitlines()]
    for name, expected in original_json.items():
        actual = [
            record["row"]
            for record in records
            if record["record_type"] == "row" and record["table"] == name
        ]
        assert actual == expected
    assert next(record for record in records if record["record_type"] == "footer")["counts"] == {
        name: len(rows) for name, rows in before.items()
    }
    assert (
        await pool.fetchval("SELECT email FROM users WHERE user_id=$1::uuid", owner)
        == "fixture@example.org"
    )
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 0
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM legacy_migration_records WHERE resource_type='session' AND disposition='isolated'"
        )
        == 2
    )
    assert (
        await pool.fetchval(
            "SELECT disposition FROM legacy_migration_records WHERE resource_type='user' AND legacy_id='not-a-uuid'"
        )
        == "isolated"
    )
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pool.execute("UPDATE legacy_reports SET content='{}'")
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pool.execute("DELETE FROM legacy_sessions")


@pytest.mark.asyncio
async def test_pending_migration_failure_rolls_back_ddl_and_version(pg_database, tmp_path):
    pool, _ = pg_database
    await seed_legacy(pool)
    before = await snapshot_rows(pool)
    for path in MIGRATIONS.glob("*.sql"):
        shutil.copyfile(path, tmp_path / path.name)
    (tmp_path / "0003_injected_failure.sql").write_text(
        "CREATE TABLE injected_partial_write(id integer); SELECT 1/0;", encoding="utf-8"
    )
    with pytest.raises(asyncpg.DivisionByZeroError):
        await run_migrations(pool, migrations_dir=tmp_path, backup_dir=tmp_path / "backups")
    assert await snapshot_rows(pool) == before
    assert await pool.fetchval("SELECT to_regclass('public.injected_partial_write')") is None
    assert await pool.fetchval("SELECT to_regclass('public.legacy_users')") is None
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 1
    await run_migrations(pool, backup_dir=tmp_path / "backups")
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 5


@pytest.mark.asyncio
async def test_separate_processes_do_not_race_schema_creation(pg_database):
    pool, name = pg_database
    code = """
import asyncio, os, asyncpg
from application.settings import Settings
from infrastructure.storage.migrations import run_migrations
async def main():
    pool = await asyncpg.create_pool(Settings.load().database_url.get_secret_value(),
                                   database=os.environ['DR4A_MIGRATION_TEST_DATABASE'], min_size=1, max_size=1)
    try:
        await run_migrations(pool)
    finally:
        await pool.close()
asyncio.run(main())
"""
    env = os.environ | {"DR4A_MIGRATION_TEST_DATABASE": name}
    processes = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        for _ in range(2)
    ]
    outputs = await asyncio.gather(*(process.communicate() for process in processes))
    assert [process.returncode for process in processes] == [0, 0], [
        error.decode() for _, error in outputs
    ]
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 5
    assert await pool.fetchval("SELECT count(*) FROM sessions") == 0


@pytest.mark.asyncio
async def test_nonempty_legacy_upgrade_requires_verified_backup(pg_database):
    pool, _ = pg_database
    await seed_legacy(pool)
    before = await snapshot_rows(pool)
    with pytest.raises(RuntimeError, match="backup directory"):
        await run_migrations(pool)
    assert await snapshot_rows(pool) == before
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 1


async def seed_mono_run(pool):
    owner, session, run = uuid4(), uuid4(), uuid4()
    brief = {
        "task_type": "evaluation_design",
        "assumptions": "",
        **{
            key: "Synthetic scope and boundary"
            for key in (
                "decision_goal",
                "research_object",
                "scope",
                "comparison_scope",
                "claims_to_verify",
                "evidence_requirements",
                "conclusion_boundary",
                "deliverable",
            )
        },
    }
    state = PipelineState.initial(
        session_id=session,
        run_id=run,
        brief_version=1,
        research_brief=brief,
        source_selection={},
        config=Settings().run_config_snapshot(),
    )
    content = state.research_brief.model_dump_json()
    sources = state.source_selection.model_dump_json()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO users(user_id,email,password_hash,is_development) VALUES($1,$2,'fixture-hash',false)",
            owner,
            owner.hex + "@example.org",
        )
        await conn.execute(
            "INSERT INTO sessions(session_id,owner_id,query,status,revision,brief_draft,brief_version,"
            "pending_questions,missing_fields,clarification_round,clarification_limit_reached,"
            "source_selection,run_id,created_at,updated_at) "
            "VALUES($1,$2,'Synthetic query','ready',2,$3::jsonb,1,'[]','[]',0,false,$4::jsonb,$5,now(),now())",
            session,
            owner,
            content,
            sources,
            run,
        )
        await conn.execute(
            "INSERT INTO briefs(session_id,version,content,frozen_at,confirmed_by,content_hash,source_selection) "
            "VALUES($1,1,$2::jsonb,now(),$3,$4,$5::jsonb)",
            session,
            content,
            owner,
            state.brief_hash,
            sources,
        )
        await conn.execute(
            "INSERT INTO research_runs(run_id,session_id,brief_version,brief_hash,status,phase,"
            "attempt_count,checkpoint_seq,lease_token,resume_allowed,config_snapshot,created_at) "
            "VALUES($1,$2,1,$3,'ready','plan',0,1,0,false,$4::jsonb,now())",
            run,
            session,
            state.brief_hash,
            state.run_metadata.config.model_dump_json(),
        )
        await conn.execute(
            "INSERT INTO phase_snapshots(snapshot_id,run_id,session_id,seq,schema_version,phase,state,state_hash,created_at) "
            "VALUES($1,$2,$3,1,1,'plan',$4::jsonb,$5,now())",
            uuid4(),
            run,
            session,
            state.model_dump_json(),
            canonical_hash(state),
        )
    return owner, session, run, state


@pytest.mark.asyncio
async def test_real_uuid_foreign_keys_sequences_and_immutability(pg_database):
    pool, _ = pg_database
    await run_migrations(pool)
    owner, session, run, state = await seed_mono_run(pool)
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs WHERE run_id=$1", run) == 1
    assert await pool.fetchval("SELECT run_id FROM sessions WHERE session_id=$1", session) == run
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE sessions SET status='clarify' WHERE session_id=$1", session)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE research_runs SET phase='completed' WHERE run_id=$1", run)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE research_runs SET lease_owner='worker' WHERE run_id=$1", run)
    with pytest.raises(asyncpg.NotNullViolationError):
        await pool.execute("UPDATE sessions SET owner_id=NULL WHERE session_id=$1", session)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute("UPDATE sessions SET owner_id=$1 WHERE session_id=$2", uuid4(), session)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pool.execute("UPDATE briefs SET content='{}' WHERE session_id=$1", session)
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pool.execute("UPDATE phase_snapshots SET state='{}' WHERE run_id=$1", run)
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO phase_snapshots(snapshot_id,run_id,session_id,seq,schema_version,phase,state,state_hash,created_at) "
            "SELECT $1,run_id,session_id,seq,schema_version,phase,state,state_hash,created_at FROM phase_snapshots WHERE run_id=$2",
            uuid4(),
            run,
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO research_runs(run_id,session_id,brief_version,brief_hash,status,phase,attempt_count,"
            "checkpoint_seq,lease_token,resume_allowed,config_snapshot,created_at) "
            "SELECT $1,session_id,brief_version,brief_hash,status,phase,attempt_count,checkpoint_seq,"
            "lease_token,resume_allowed,config_snapshot,created_at FROM research_runs WHERE run_id=$2",
            uuid4(),
            run,
        )
    await pool.execute(
        "INSERT INTO messages(message_id,session_id,sequence,role,kind,content,brief_version,created_at) "
        "VALUES($1,$2,1,'user','confirmation','Synthetic confirmation',1,now())",
        uuid4(),
        session,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO messages(message_id,session_id,sequence,role,kind,content,brief_version,created_at) "
            "VALUES($1,$2,1,'user','confirmation','Synthetic duplicate',1,now())",
            uuid4(),
            session,
        )
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pool.execute("DELETE FROM messages WHERE session_id=$1", session)
    await pool.execute(
        "INSERT INTO idempotency_requests(owner_id,operation,key,request_hash,state,lease_expires_at) "
        "VALUES($1,'confirm','same-key',$2,'in_progress',now()+interval '120 seconds')",
        owner,
        "a" * 64,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO idempotency_requests(owner_id,operation,key,request_hash,state,lease_expires_at) "
            "VALUES($1,'confirm','same-key',$2,'in_progress',now()+interval '120 seconds')",
            owner,
            "a" * 64,
        )
    assert json.loads(
        await pool.fetchval("SELECT state FROM phase_snapshots WHERE run_id=$1", run)
    ) == state.model_dump(mode="json")


@pytest.mark.asyncio
async def test_current_checkpoint_and_brief_must_exist_at_commit(pg_database):
    pool, _ = pg_database
    await run_migrations(pool)
    _, session, run, _ = await seed_mono_run(pool)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute("UPDATE research_runs SET checkpoint_seq=2 WHERE run_id=$1", run)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute("UPDATE sessions SET brief_version=2 WHERE session_id=$1", session)
    assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs WHERE run_id=$1", run) == 1


@pytest.mark.asyncio
async def test_ambiguous_user_identities_are_not_merged(pg_database, tmp_path):
    pool, _ = pg_database
    await run_migrations(pool, through_version="0001_init")
    first, second = uuid4(), uuid4()
    await pool.execute(
        "INSERT INTO users(user_id,email,password_hash) VALUES($1,'Collision@example.org','fixture-hash'),"
        "($2,'collision@example.org','fixture-hash')",
        first.hex,
        second.hex,
    )
    await run_migrations(pool, backup_dir=tmp_path)
    assert await pool.fetchval("SELECT count(*) FROM users") == 0
    assert await pool.fetchval("SELECT count(*) FROM legacy_users") == 2
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM legacy_migration_records WHERE reason='normalized_email_collision'"
        )
        == 2
    )
    with pytest.raises(RuntimeError, match="newer or unknown"):
        await run_migrations(pool, through_version="0001_init")


@pytest.mark.asyncio
async def test_failed_backup_does_not_change_legacy_database(pg_database, tmp_path):
    pool, _ = pg_database
    await seed_legacy(pool)
    before = await snapshot_rows(pool)
    destination = tmp_path / "not-a-directory"
    destination.write_text("synthetic path conflict", encoding="utf-8")
    with pytest.raises(FileExistsError):
        await run_migrations(pool, backup_dir=destination)
    assert await snapshot_rows(pool) == before
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 1


@pytest.mark.asyncio
async def test_report_tool_identity_and_cross_session_constraints(pg_database):
    pool, _ = pg_database
    await run_migrations(pool)
    _, session, run, _ = await seed_mono_run(pool)
    _, other_session, other_run, _ = await seed_mono_run(pool)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute("UPDATE sessions SET run_id=$1 WHERE session_id=$2", other_run, session)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(
            "INSERT INTO reports(report_id,run_id,session_id,version,content,created_at) "
            "VALUES($1,$2,$3,1,'{}',now())",
            uuid4(),
            run,
            other_session,
        )
    report = uuid4()
    await pool.execute(
        "INSERT INTO reports(report_id,run_id,session_id,version,content,created_at) "
        "VALUES($1,$2,$3,1,'{\"fixture\":true}',now())",
        report,
        run,
        session,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO reports(report_id,run_id,session_id,version,content,created_at) "
            "VALUES($1,$2,$3,2,'{}',now())",
            uuid4(),
            run,
            session,
        )
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await pool.execute("DELETE FROM reports WHERE report_id=$1", report)
    await pool.execute(
        "INSERT INTO tool_calls(call_id,run_id,call_key,status,request_hash,budget_units,created_at,updated_at) "
        "VALUES('call1',$1,'key1','reserved',$2,1,now(),now())",
        run,
        "a" * 64,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO tool_calls(call_id,run_id,call_key,status,request_hash,budget_units,created_at,updated_at) "
            "VALUES('call2',$1,'key1','reserved',$2,1,now(),now())",
            run,
            "a" * 64,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE tool_calls SET status='succeeded' WHERE call_id='call1'")
    await pool.execute(
        "UPDATE tool_calls SET status='succeeded',result_object_key='fixture/cache',result_hash=$1 "
        "WHERE call_id='call1'",
        "b" * 64,
    )
    assert await pool.fetchval("SELECT count(*) FROM tool_calls") == 1


@pytest.mark.asyncio
async def test_checkpoint_cannot_change_frozen_brief_or_run_configuration(pg_database):
    pool, _ = pg_database
    await run_migrations(pool)
    _, session, run, state = await seed_mono_run(pool)
    data = state.model_dump(mode="json")
    for changed in (
        data | {"brief_hash": "0" * 64},
        data | {"brief_version": 2},
        data | {"run_metadata": data["run_metadata"] | {"config": {"changed": True}}},
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO phase_snapshots(snapshot_id,run_id,session_id,seq,schema_version,phase,state,state_hash,created_at) "
                "VALUES($1,$2,$3,2,1,'plan',$4::jsonb,$5,now())",
                uuid4(),
                run,
                session,
                json.dumps(changed),
                canonical_hash(changed),
            )
    assert await pool.fetchval("SELECT count(*) FROM phase_snapshots WHERE run_id=$1", run) == 1
