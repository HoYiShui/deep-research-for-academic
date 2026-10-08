"""Real CLI parsing/profile routing; refuses before I/O, never opens user databases."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from tests.unit.test_state import initial_state


@pytest.mark.parametrize("mode", ["anonymous", "authenticated", "production", "fake"])
async def test_debug_run_database_selection_is_explicit_safe_and_preserves_parser(tmp_path, mode):
    brief = tmp_path / "brief.json"
    brief.write_text(initial_state().research_brief.model_dump_json())
    script = """
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit
import asyncpg
from application.settings import Settings
from cli.__main__ import main
from scripts import debug_backend
mode=sys.argv[1]
settings=Settings(database_url='postgresql://owner:private-password-canary@localhost:5432/original?sslmode=require',
    anthropic_api_key='controlled-key',parser_version='explicit-parser-version',
    dr4a_auth_required=mode=='authenticated')
if mode=='production':
    settings=SimpleNamespace(dr4a_env='production',dr4a_auth_required=True)
Settings.load=classmethod(lambda cls:settings)
original=debug_backend.debug_settings
def profile(value,**kwargs):
    result=original(value,**kwargs)
    assert result.parser_version=='explicit-parser-version'
    assert result.dr4a_debug_runner is False
    return result
debug_backend.debug_settings=profile
calls=[]
async def refusing_pool(url,**kwargs):
    assert mode=='anonymous'
    parsed=urlsplit(url)
    assert parsed.path=='/dr4a_debug' and parsed.query=='sslmode=require'
    assert parsed.username=='owner' and parsed.password=='private-password-canary'
    calls.append(url)
    raise OSError('private-password-canary')
asyncpg.create_pool=refusing_pool
arguments=['run','--brief',sys.argv[2],'--debug-db','--json']
if mode!='fake': arguments.append('--real')
result=main(arguments)
assert len(calls)==(1 if mode=='anonymous' else 0)
sys.exit(result)
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        mode,
        str(brief),
        cwd=Path(__file__).resolve().parents[2],
        env=dict(os.environ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    body = json.loads(stdout)
    assert process.returncode == (3 if mode == "anonymous" else 2)
    assert body["error"]["code"] == (
        "dependency_unavailable" if mode == "anonymous" else "validation_error"
    )
    assert b"canary" not in stdout + stderr and b"Traceback" not in stderr
    assert "session_id" not in body
