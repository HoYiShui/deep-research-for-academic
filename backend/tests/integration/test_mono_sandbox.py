"""Actual development Docker sandbox; no mocks, model calls, PG or user volumes."""

import asyncio
import hashlib

import pytest

from domain.ports import AdapterError
from infrastructure.sandbox.docker import DockerExecution


async def container_ids():
    process = await asyncio.create_subprocess_exec(
        "docker",
        "ps",
        "--all",
        "--filter",
        "label=dr4a.sandbox=true",
        "--format",
        "{{.ID}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(process.communicate(), 10)
    assert process.returncode == 0
    return set(stdout.decode().split())


async def test_real_script_input_and_nonroot_bounded_attachment_roundtrip():
    before = await container_ids()
    code = """
import json,os,pathlib,socket
data=json.loads(pathlib.Path('/work/input.json').read_text())
assert os.getuid()==65534
for path in ('/work/input.json','/work/script.py','/etc/dr4a-test-write'):
    try:
        with open(path,'w') as stream: stream.write('unsafe')
    except OSError: pass
    else: raise RuntimeError('unexpected writable input/root')
try:
    socket.create_connection(('1.1.1.1',443),timeout=1)
except OSError: pass
else: raise RuntimeError('unexpected outbound network')
out=pathlib.Path('/work/out')
out.joinpath('rows.csv').write_text('method,value\\nA,3\\n')
out.joinpath('result.json').write_text(json.dumps({'sum':sum(data['values']),'uid':os.getuid(),'files':['rows.csv']}))
print('private-template-log-canary')
"""
    value = await DockerExecution().execute_checked(code, {"values": [1, 2]}, 20)
    assert value.output == {"sum": 3, "uid": 65534, "files": ["rows.csv"]}
    assert value.files[0].sha256 == hashlib.sha256(b"method,value\nA,3\n").hexdigest()
    assert value.files[0].body == b"method,value\nA,3\n"
    assert await container_ids() == before


@pytest.mark.parametrize(
    "script",
    [
        "pass",
        "raise RuntimeError('private-error-canary')",
        "from pathlib import Path; Path('/work/out/result.json').symlink_to('/work/input.json')",
        "import json,pathlib; pathlib.Path('/work/out/result.json').write_text(json.dumps({'files':['../input.json']}))",
        "import json,pathlib; p=pathlib.Path('/work/out'); p.joinpath('leak.csv').symlink_to('/work/input.json'); p.joinpath('result.json').write_text(json.dumps({'files':['leak.csv']}))",
        "import pathlib; p=pathlib.Path('/work/out'); p.joinpath('big.csv').write_bytes(b'x'*(10*1024*1024+1)); p.joinpath('result.json').write_text('{\"files\":[\"big.csv\"]}')",
    ],
)
async def test_real_missing_failed_or_unsafe_output_never_becomes_empty_success(script):
    before = await container_ids()
    with pytest.raises(AdapterError) as error:
        await DockerExecution().execute_checked(script, {}, 20)
    assert error.value.code in {"execution_failed", "output_invalid"}
    assert "canary" not in str(error.value)
    assert await container_ids() == before


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_or_task_cancellation_removes_actual_container(cancel):
    before = await container_ids()
    task = asyncio.create_task(DockerExecution().execute("import time; time.sleep(60)", {}, 3))
    if cancel:
        async with asyncio.timeout(10):
            while await container_ids() == before:
                await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(AdapterError, match="execution_timeout"):
            await task
    assert await container_ids() == before
