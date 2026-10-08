"""Local TUI backend on a separate mono debug database; preserves the old DB."""

import argparse
import asyncio
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import uvicorn
from pydantic import SecretStr

from application.errors import AppError
from application.settings import Settings
from infrastructure.parser.html import HTML_PARSER_VERSION
from infrastructure.parser.mineru_output import MINERU_PARSER_VERSION
from interface.main import create_app


def debug_settings(settings, *, execute=False, parser="html"):
    if settings.dr4a_env != "development" or settings.dr4a_auth_required:
        raise ValueError("This helper requires anonymous development configuration")
    parsed = urlsplit(settings.database_url.get_secret_value())
    if parsed.path.rstrip("/") == "/dr4a_debug":
        database_url = settings.database_url
    else:
        database_url = SecretStr(urlunsplit(parsed._replace(path="/dr4a_debug")))
    values = settings.model_dump() | {
        "database_url": database_url,
        "dr4a_debug_runner": execute,
    }
    if parser is not None:
        values["parser_version"] = (
            HTML_PARSER_VERSION if parser == "html" else MINERU_PARSER_VERSION
        )
    return Settings.model_validate(values)


async def serve(args):
    source = Settings.load()
    config = debug_settings(source, execute=args.execute, parser=args.parser)
    connection = await asyncpg.connect(source.database_url.get_secret_value(), timeout=10)
    try:
        if not await connection.fetchval("SELECT 1 FROM pg_database WHERE datname='dr4a_debug'"):
            await connection.execute('CREATE DATABASE "dr4a_debug"')
    finally:
        await connection.close()
    # Refuse an unrelated existing database. Never migrate rescued user data.
    check = await asyncpg.connect(config.database_url.get_secret_value(), timeout=10)
    try:
        tables = await check.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        if tables and not await check.fetchval(
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
        ):
            raise ValueError("Existing dr4a_debug is not a project database")
        if tables and not await check.fetchval(
            "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version='0002_mono_research')"
        ):
            raise ValueError("Existing dr4a_debug is not mono; no automatic legacy migration")
    finally:
        await check.close()
    app = create_app(settings=config)
    print(
        f"Debug API http://127.0.0.1:{args.port} | database dr4a_debug | executor {'plan/research' if args.execute else 'manual'}",
        flush=True,
    )
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port)).serve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--execute", action="store_true", help="paid real plan/research; missing later workers fail"
    )
    parser.add_argument("--parser", choices=["html", "pdf"], default="html")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    except AppError as exc:
        parser.exit(1, f"{exc.code}: {exc.message}\n")
    except (asyncpg.PostgresError, OSError, TimeoutError):
        parser.exit(
            1,
            "Debug backend unavailable: check PostgreSQL/configuration; original database was not migrated.\n",
        )


if __name__ == "__main__":
    main()
