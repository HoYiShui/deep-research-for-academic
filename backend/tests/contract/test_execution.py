"""Sandbox envelope/input safety; live Docker behavior has separate integration tests."""

import base64
import hashlib
import json
from unittest.mock import patch

import pytest

from domain.ports import AdapterError
from infrastructure.sandbox.docker import DockerExecution, decode_result


class _FakeProc:
    returncode = 0
    stdout = None

    async def wait(self):
        return 0

    def kill(self):
        pass


@pytest.mark.asyncio
async def test_docker_execute_returns_empty_when_no_result() -> None:
    from asyncio import StreamReader

    proc = _FakeProc()
    proc.stdout = StreamReader()
    proc.stdout.feed_eof()
    with patch(
        "infrastructure.sandbox.docker.asyncio.create_subprocess_exec",
        return_value=proc,
    ) as mock_exec:
        adapter = DockerExecution()
        with pytest.raises(AdapterError, match="output_invalid"):
            await adapter.execute("print(1)", {"x": 1})
        assert mock_exec.called


def test_attachment_hash_size_media_and_original_bytes_are_checked():
    raw = b"method,value\nA,1\n"
    result = decode_result(
        json.dumps(
            {
                "output": {"files": ["rows.csv"]},
                "files": [
                    {"name": "rows.csv", "body": base64.b64encode(raw).decode()},
                ],
            }
        ).encode()
    )
    assert result.files[0].body == raw
    assert result.files[0].sha256 == hashlib.sha256(raw).hexdigest()
    assert result.files[0].size == len(raw) and result.files[0].media_type == "text/csv"


@pytest.mark.parametrize(
    "name,body",
    [
        ("../escape.csv", b"x"),
        ("nested/a.csv", b"x"),
        ("/absolute.csv", b"x"),
        ("payload.svg", b"<script/>"),
        ("chart.png", b"not-png"),
        ("rows.csv", b"\xff"),
        ("data.json", b"NaN"),
        ("result.json", b"{}"),
    ],
)
def test_invalid_attachment_envelopes_are_rejected(name, body):
    envelope = {
        "output": {"files": [name]},
        "files": [{"name": name, "body": base64.b64encode(body).decode()}],
    }
    with pytest.raises(AdapterError, match="output_invalid"):
        decode_result(json.dumps(envelope).encode())


@pytest.mark.parametrize(
    "output",
    [
        b"",
        b"{}",
        b"[]",
        b'{"output":{},"files":[],"extra":1}',
        b'{"output":{"value":NaN},"files":[]}',
    ],
)
def test_invalid_or_nonfinite_result_is_not_empty_success(output):
    with pytest.raises(AdapterError, match="output_invalid"):
        decode_result(output)


async def test_invalid_input_is_rejected_before_docker_io():
    with patch("infrastructure.sandbox.docker.asyncio.create_subprocess_exec") as process:
        for code, value, timeout in [
            ("", {}, 1),
            ("pass", {"x": float("nan")}, 1),
            ("pass", {}, 0),
        ]:
            with pytest.raises(AdapterError, match="input_invalid"):
                await DockerExecution().execute(code, value, timeout)
        process.assert_not_called()
