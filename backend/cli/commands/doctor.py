"""Read-only dependency diagnostics; never charge models or migrate a database."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from application.settings import Settings
from cli import output

_REQUIRED_ENV = ("ANTHROPIC_API_KEY", "BOCHA_API_KEY", "DATABASE_URL", "JWT_SECRET")
_LIMITATIONS = [
    "This is a scoped dependency diagnostic, not full service readiness or research E2E.",
    "No paid model/search calls, model downloads, database migrations or object writes occur.",
    "Search configuration is checked, not gateway reachability or provider quality.",
    "Model inference, parser execution, full pipeline and report quality remain unverified.",
    "PG checks migration markers/tables; MinIO checks authenticated bucket access, not writes.",
]


async def run(args) -> int:
    settings = Settings.load()
    if getattr(args, "debug_db", False):
        if settings.dr4a_env != "development" or settings.dr4a_auth_required:
            raise output.UsageError("--debug-db requires anonymous development configuration")
        from scripts.debug_backend import debug_settings

        # Only select the database, not the helper's HTML parser/runner override.
        database = debug_settings(settings).database_url
        settings = settings.model_copy(update={"database_url": database})
    scope = getattr(args, "scope", "all")
    connected, schema = await _postgres_checks(settings)
    checks = [
        ("env", _check_config(settings)),
        ("postgres", connected),
        ("postgres_schema", schema),
        ("minio_bucket", await _check_bucket(settings)),
    ]
    if scope == "all":
        checks.extend(
            [
                ("model_weights", _check_model_weights(settings.bge_m3_model_path)),
                ("milvus", await _check_milvus(settings)),
            ]
        )
    failed = [name for name, ok in checks if not ok]
    status = "ok" if not failed else "env_error"
    data = {"scope": scope, "checks": dict(checks), "limitations": _LIMITATIONS}
    if failed and args.json:
        return output.emit_error(
            args,
            output.EXIT_ENV,
            "service_not_ready",
            "Failed diagnostic checks: " + ", ".join(failed),
            data=data,
        )
    if args.json:
        output.emit_json(status, data)
    else:
        body = "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" for name, ok in checks)
        output.emit_human(status, body + "\n" + "\n".join(_LIMITATIONS))
    return output.EXIT_SUCCESS if not failed else output.EXIT_ENV


def _check_env() -> bool:
    """Legacy helper retained for callers; presence is not configured content."""
    return all(os.environ.get(key, "").strip() for key in _REQUIRED_ENV)


def _check_config(settings) -> bool:
    required = [
        "database_url",
        "anthropic_api_key",
        "minio_access_key",
        "minio_secret_key",
    ]
    if settings.web_search_provider == "bocha":
        required.append("bocha_api_key")
    if settings.dr4a_auth_required:
        required.append("jwt_secret")
    return not settings.llm_local and all(
        getattr(settings, key).get_secret_value().strip() for key in required
    )


def _check_model_weights(path=None) -> bool:
    path = os.environ.get("BGE_M3_MODEL_PATH", "") if path is None else path
    # A Hub identifier cannot prove prepared local weights; never download here.
    if not path or not os.path.isabs(path) or not Path(path).is_dir():
        return False
    root = Path(path)
    return (root / "config.json").is_file() and any(
        item.is_file() and item.stat().st_size > 0
        for pattern in ("*.safetensors", "pytorch_model*.bin")
        for item in root.glob(pattern)
    )


async def _postgres_checks(settings) -> tuple[bool, bool]:
    connection = None
    connected = False
    try:
        import asyncpg

        connection = await asyncpg.connect(
            settings.database_url.get_secret_value(), timeout=5, command_timeout=5
        )
        connected = True
        async with connection.transaction(readonly=True):
            if not await connection.fetchval(
                "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
            ):
                return True, False
            migrations = Path(__file__).resolve().parents[2] / "infrastructure/storage/migrations"
            supported = {path.stem for path in migrations.glob("*.sql")}
            applied = {
                row["version"]
                for row in await connection.fetch("SELECT version FROM public.schema_migrations")
            }
            schema = await connection.fetchval(
                "SELECT to_regclass('public.research_runs') IS NOT NULL "
                "AND to_regclass('public.tool_call_attempts') IS NOT NULL "
                "AND to_regclass('public.phase_snapshots') IS NOT NULL"
            )
            return True, bool(schema and "0002_mono_research" in applied and applied == supported)
    except Exception:  # noqa: BLE001 -- never expose DSN/provider exception text
        return connected, False
    finally:
        if connection is not None:
            await connection.close(timeout=5)


async def _check_milvus(settings=None) -> bool:
    def probe():
        from pymilvus import MilvusClient

        uri = (
            settings.milvus_uri
            if settings
            else os.environ.get("MILVUS_URI", "http://localhost:19530")
        )
        # Refuse embedded Lite paths: this diagnostic is for Standalone only.
        if not uri.startswith(("http://", "https://")):
            return False
        client = MilvusClient(uri=uri, timeout=5)
        try:
            client.list_collections(timeout=5)
            return True
        finally:
            client.close()

    try:
        return await asyncio.to_thread(probe)
    except Exception:  # noqa: BLE001
        return False


async def _check_bucket(settings) -> bool:
    def probe():
        import urllib3
        from minio import Minio

        transport = urllib3.PoolManager(timeout=urllib3.Timeout(connect=3, read=5), retries=False)
        try:
            client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key.get_secret_value(),
                secret_key=settings.minio_secret_key.get_secret_value(),
                secure=settings.minio_secure,
                http_client=transport,
            )
            return client.bucket_exists(settings.minio_bucket)
        finally:
            transport.clear()

    try:
        return await asyncio.to_thread(probe)
    except Exception:  # noqa: BLE001
        return False
