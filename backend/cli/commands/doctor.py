"""doctor command: environment check (exit 3 on any failure)."""

from __future__ import annotations

import os

from cli import output

_REQUIRED_ENV = ("ANTHROPIC_API_KEY", "BOCHA_API_KEY", "DATABASE_URL", "JWT_SECRET")


async def run(args) -> int:
    checks = [
        ("env", _check_env()),
        ("model_weights", _check_model_weights()),
        ("postgres", await _check_postgres()),
        ("milvus", await _check_milvus()),
        ("minio", await _check_minio()),
    ]
    failed = [name for name, ok in checks if not ok]
    status = "ok" if not failed else "env_error"
    if args.json:
        output.emit_json(status, {"checks": {name: ok for name, ok in checks}})
    else:
        body = "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}" for name, ok in checks)
        output.emit_human(status, body)
    return output.EXIT_SUCCESS if not failed else output.EXIT_ENV


def _check_env() -> bool:
    return all(k in os.environ for k in _REQUIRED_ENV)


def _check_model_weights() -> bool:
    path = os.environ.get("BGE_M3_MODEL_PATH", "")
    if not path or not os.path.isabs(path):
        return True  # HF hub name (downloaded on first use), not a local-path failure
    return os.path.isdir(path)


async def _check_postgres() -> bool:
    try:
        import asyncpg

        conn = await asyncpg.connect(os.environ.get("DATABASE_URL", ""))
        await conn.close()
        return True
    except Exception:  # noqa: BLE001 — probe; any failure is a FAIL
        return False


async def _check_milvus() -> bool:
    try:
        from pymilvus import MilvusClient

        MilvusClient(uri=os.environ.get("MILVUS_URI", "http://localhost:19530")).list_collections()
        return True
    except Exception:  # noqa: BLE001
        return False


async def _check_minio() -> bool:
    try:
        import httpx

        endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://{endpoint}/minio/health/live")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False
