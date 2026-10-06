"""Real CLI processes with canonical input and explicit controlled dependencies."""

import asyncio
import json
import os
import sys
from pathlib import Path

from cli.phase_tools import DebugTools
from domain.research.phase_contracts import PhaseInput
from domain.research.state import PipelineState
from tests.unit.test_state import initial_state


async def invoke(path, phase="plan", *, real=False, env=None):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "cli",
        "phase",
        phase,
        "--state",
        str(path),
        "--seed",
        "42",
        "--json",
        *(["--real"] if real else []),
        cwd=Path(__file__).resolve().parents[2],
        env=dict(os.environ, DATABASE_URL="postgresql://invalid-no-db/debug", **(env or {})),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return process.returncode, json.loads(stdout), stderr.decode()


async def test_fake_plan_uses_canonical_merge_and_is_deterministic_without_pg(tmp_path):
    state = initial_state()
    path = tmp_path / "state.json"
    path.write_text(state.model_dump_json())
    before = path.read_bytes()
    code, body, stderr = await invoke(path)
    assert code == 0 and stderr == ""
    assert body["dependency_mode"] == "fake" and body["error"] is None
    assert body["debug_usage"]["llm_calls"] == 0
    assert set(body["state_delta"]) == {"section_plans"}
    assert body["state"]["phase"] == "plan"
    assert body["state"]["run_metadata"] == state.run_metadata.model_dump(mode="json")
    post = PipelineState.model_validate(body["state"])
    PhaseInput.from_state(post)
    assert len(post.section_plans) == 5 and post.final_report is None
    assert path.read_bytes() == before
    second = await invoke(path)
    assert (code, body, stderr) == second


async def test_valid_research_input_fails_explicitly_until_worker_registered(tmp_path):
    state = initial_state()
    path = tmp_path / "state.json"
    path.write_text(state.model_dump_json())
    _, body, _ = await invoke(path)
    data = body["state"] | {"phase": "research"}
    path.write_text(json.dumps(data))
    code, body, _ = await invoke(path, "research")
    assert code == 3 and body["error"]["code"] == "service_not_ready"
    assert "state" not in body


async def test_real_sdk_plan_against_controlled_http_not_a_real_model_claim(tmp_path):
    state = initial_state()
    path = tmp_path / "state.json"
    path.write_text(state.model_dump_json())
    calls = []
    response_text = DebugTools(state, fake=True, seed=42).fake_plan()

    async def handler(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            request = json.loads(await reader.readexactly(length))
            calls.append(request)
            body = json.dumps(
                {
                    "id": "controlled-response",
                    "type": "message",
                    "role": "assistant",
                    "model": state.run_metadata.config.versions.llm_model,
                    "content": [{"type": "text", "text": response_text}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 20, "output_tokens": 30},
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        code, body, stderr = await invoke(
            path,
            real=True,
            env={
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
                "ANTHROPIC_API_KEY": "controlled-test-key",
                "LLM_MODEL": state.run_metadata.config.versions.llm_model,
                "LLM_REVISION": state.run_metadata.config.versions.llm_revision,
                "LLM_LOCAL": "false",
                "NO_PROXY": "127.0.0.1,localhost",
            },
        )
    assert code == 0 and stderr == ""
    assert len(calls) == 1 and "FROZEN" in calls[0]["messages"][0]["content"]
    assert body["debug_usage"] == {"llm_calls": 1, "input_tokens": 20, "output_tokens": 30}
    assert len(body["state"]["section_plans"]) == 5
    assert body["state"]["run_metadata"]["budget_used"]["llm_calls"] == 0
