"""Read current owner-scoped mono checkpoint without migrating or executing."""

import asyncio
import json
from uuid import UUID

import asyncpg
from pydantic import ValidationError

from application.errors import AppError
from application.records import DEVELOPMENT_USER_ID
from application.research_queries import ResearchQueries
from application.settings import Settings
from cli import output
from domain.ports import AdapterError
from infrastructure.storage.research_postgres import PostgresResearchStore


async def run(args) -> int:
    try:
        session_id = UUID(args.session_id)
        explicit_owner = getattr(args, "owner", None)
        owner = UUID(explicit_owner) if explicit_owner else DEVELOPMENT_USER_ID
    except ValueError:
        raise output.UsageError("Invalid session or owner UUID") from None
    try:
        settings = Settings.load()
    except ValidationError:
        raise output.EnvError("Invalid environment configuration") from None
    if explicit_owner is None and settings.dr4a_env == "production":
        raise output.UsageError("Production dump requires --owner UUID")
    pool = None
    try:
        pool = await asyncpg.create_pool(
            settings.database_url.get_secret_value(), min_size=1, max_size=2, timeout=10
        )
        if not await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version='0002_mono_research')"
        ):
            raise AdapterError(
                "postgres",
                "schema_incompatible",
                "Database requires explicit mono migration",
                False,
                "dump",
            )
        store = PostgresResearchStore(pool)
        if await store.users.get_by_id(owner) is None:
            raise AppError("owner_not_found", "CLI owner does not exist")
        result = await ResearchQueries(store, store.research).checkpoint_view(owner, session_id)
        if args.json:
            output.emit_json("ok", {"error": None, **result})
        else:
            output.emit_human("ok", json.dumps(result, ensure_ascii=False, indent=2))
        return output.EXIT_SUCCESS
    except AppError as exc:
        return output.emit_error(args, output.EXIT_FAILURE, exc.code, exc.message, exc.retryable)
    except AdapterError as exc:
        return output.emit_error(args, output.EXIT_ENV, exc.code, exc.message, exc.retryable)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        return output.emit_error(
            args,
            output.EXIT_ENV,
            "schema_incompatible",
            "Database schema requires explicit migration",
        )
    except (asyncpg.PostgresError, OSError, TimeoutError):
        return output.emit_error(
            args,
            output.EXIT_ENV,
            "database_unavailable",
            "Database is unavailable or incompatible",
            True,
        )
    finally:
        if pool is not None:
            try:
                await asyncio.wait_for(pool.close(), timeout=5)
            except TimeoutError:
                pool.terminate()
