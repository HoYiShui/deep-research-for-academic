"""Frozen CLI intake and lease-scoped mono execution; no legacy fallback."""

import asyncio
import signal
from uuid import UUID, uuid4

import asyncpg

from application.bootstrap import HttpRuntime
from application.debug_runtime import PublicResearchExecution
from application.errors import AppError
from application.records import DEVELOPMENT_USER_ID, ClaimedRun
from application.run_sse import RunEventStream
from application.settings import Settings
from application.task_runner import TaskRunner
from cli import output
from domain.ports import AdapterError
from domain.research.models import ResearchBrief, SourceSelection
from infrastructure.storage.research_postgres import PostgresResearchStore


async def cancel_owned(worker, store, owner, run_id):
    """Signal cancellation is fenced in the same transaction as its mutation."""
    async with store.transaction() as tx:
        run = await store.research.get_run(owner, run_id, tx)
        if (
            run is None
            or run.lease_owner != worker.worker_id
            or run.status not in {"running", "cancelling"}
        ):
            return False
        claimed = ClaimedRun(owner_id=owner, run=run)
        try:
            await store.research.check_run_lease(claimed, tx)
        except AppError as exc:
            if exc.code == "stale_resource":
                return False
            raise
        await store.research.request_cancel(owner, run.session_id, tx)
    return True


async def run(args, raw_brief):
    brief = ResearchBrief.model_validate(raw_brief)
    settings = Settings.load()
    try:
        explicit_owner = getattr(args, "owner", None)
        owner = UUID(explicit_owner) if explicit_owner else DEVELOPMENT_USER_ID
        categories = [value.strip() for value in getattr(args, "sources", "papers,web").split(",")]
        selection = SourceSelection(
            categories=categories,
            knowledge_base_ids=[UUID(value) for value in getattr(args, "kb", [])],
        )
    except ValueError:
        raise output.UsageError("Invalid owner or source selection") from None
    if settings.dr4a_env == "production" and explicit_owner is None:
        raise output.UsageError("Production run requires --owner UUID")
    if settings.llm_local:
        raise output.EnvError("Local model CLI adapter is not configured")
    if not settings.anthropic_api_key.get_secret_value() and not settings.llm_local:
        raise output.EnvError("Model API key is not configured")
    pool, runtime, execution, worker = None, None, None, None
    installed = []
    stop = asyncio.Event()
    events = []
    accepted_identity = None
    try:
        pool = await asyncpg.create_pool(
            settings.database_url.get_secret_value(), min_size=1, max_size=6, timeout=10
        )
        if not await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version='0002_mono_research')"
        ):
            raise AdapterError(
                "postgres",
                "schema_incompatible",
                "Database requires explicit mono migration",
                False,
                "run",
            )
        store = PostgresResearchStore(pool)
        if explicit_owner is not None and await store.users.get_by_id(owner) is None:
            raise AppError("owner_not_found", "CLI owner does not exist")
        runtime = HttpRuntime(settings=settings, research_store=store)
        await runtime.prepare(start_runner=False)
        if await store.users.get_by_id(owner) is None:
            raise AppError("owner_not_found", "CLI owner does not exist")

        def committed(value):
            point, run = value.checkpoint, value.claimed.run
            frame = RunEventStream._projection(
                {
                    "session_id": str(run.session_id),
                    "run_id": str(run.run_id),
                    "checkpoint_seq": point.seq,
                    "phase": point.phase,
                    "status": run.status,
                }
            )
            events.append(frame.model_dump(mode="json"))

        try:
            execution = PublicResearchExecution(
                runtime,
                committed=committed,
                diagnostic=lambda event: events.append(event.model_dump(mode="json")),
            )
        except AppError as exc:
            if exc.code == "service_not_ready":
                raise output.EnvError(exc.message) from None
            raise
        accepted = await runtime.research.start_frozen(owner, brief, selection, str(uuid4()))
        session_id, run_id = UUID(accepted.body["session_id"]), UUID(accepted.body["run_id"])
        accepted_identity = {
            "session_id": str(session_id),
            "run_id": str(run_id),
            "dependency_mode": "real",
        }
        worker = TaskRunner(
            store=store, execute=execution.execute, settings=settings, owner=owner, run_id=run_id
        )
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            try:
                previous = signal.getsignal(name)
                loop.add_signal_handler(name, stop.set)
                installed.append((name, previous))
            except NotImplementedError:
                pass
        await worker.start()
        async with asyncio.timeout(
            settings.queue_timeout_s + settings.run_deadline_s + settings.shutdown_s
        ):
            while True:
                view = await runtime.research_queries.session_view(owner, session_id)
                if view["status"] in {"completed", "failed", "cancelled"}:
                    break
                if stop.is_set():
                    cancelled = await cancel_owned(worker, store, owner, run_id)
                    await worker.aclose()
                    view = await runtime.research_queries.session_view(owner, session_id)
                    if not cancelled and view["status"] not in {"completed", "failed", "cancelled"}:
                        raise AppError("interrupted", "CLI stopped observing a Run owned elsewhere")
                    break
                try:
                    await asyncio.wait_for(stop.wait(), timeout=min(settings.scan_s, 1))
                except TimeoutError:
                    pass
        if view["status"] not in {"completed", "failed", "cancelled"}:
            raise AppError(
                "interrupted", "Cancellation requested; terminal state is not yet committed"
            )
        events.append(RunEventStream._projection(view).model_dump(mode="json"))
        report = None
        if view["status"] == "completed":
            await runtime.research_queries.report_view(owner, session_id)
            report = await store.research.load_report(owner, run_id)
        result = {
            "session_id": str(session_id),
            "run_id": str(run_id),
            "final_report": report.model_dump(mode="json") if report is not None else None,
            "checkpoint_seq": view["checkpoint_seq"],
            "phase": view["phase"],
            "dependency_mode": "real",
        }
        if not args.quiet:
            result["events"] = events
        if report is None:
            failure = view["failure"] or {"code": "cancelled", "message": "Run cancelled"}
            code = (
                3
                if failure["code"]
                in {
                    "service_not_ready",
                    "config_unavailable",
                    "schema_incompatible",
                    "dependency_unavailable",
                }
                else 1
            )
            return output.emit_error(
                args,
                code,
                failure["code"],
                failure["message"],
                failure.get("retryable", False),
                data=result,
            )
        if args.json:
            output.emit_json("ok", result)
        else:
            output.emit_human("ok", report.markdown)
        return 0
    except (AppError, AdapterError) as exc:
        if accepted_identity is None:
            raise
        code = 3 if isinstance(exc, AdapterError) else 1
        return output.emit_error(
            args, code, exc.code, exc.message, exc.retryable, data=accepted_identity
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        if accepted_identity is not None:
            return output.emit_error(
                args,
                3,
                "schema_incompatible",
                "Database schema is incompatible",
                data=accepted_identity,
            )
        raise AdapterError(
            "postgres",
            "schema_incompatible",
            "Database requires explicit mono migration",
            False,
            "run",
        ) from None
    except (asyncpg.PostgresError, OSError, TimeoutError):
        if accepted_identity is not None:
            return output.emit_error(
                args,
                3,
                "dependency_unavailable",
                "Database unavailable or Run wait expired",
                True,
                data=accepted_identity,
            )
        raise AdapterError(
            "postgres",
            "dependency_unavailable",
            "Database unavailable or Run wait expired",
            True,
            "run",
        ) from None
    finally:
        for name, previous in installed:
            asyncio.get_running_loop().remove_signal_handler(name)
            signal.signal(name, previous)
        try:
            if worker is not None:
                await worker.aclose()
        finally:
            try:
                if execution is not None:
                    await execution.aclose()
            finally:
                try:
                    if runtime is not None:
                        await runtime.aclose()
                finally:
                    if pool is not None:
                        try:
                            await asyncio.wait_for(pool.close(), timeout=5)
                        except TimeoutError:
                            pool.terminate()
