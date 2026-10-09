"""Opt-in diagnostics do not change execution, accounting, or public events."""

import asyncio
import json
import stat

import pytest
from pydantic import BaseModel, model_validator

from cli import output
from cli.__main__ import build_parser, execute
from cli.trace import TraceRecorder
from domain.ports import AdapterError
from domain.research.agents.structured import complete
from domain.research.diagnostics import diagnostic, diagnostic_scope, result_summary


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_exclusive_private_redacted_trace(tmp_path):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path, secrets=["configured-secret"])
    with diagnostic_scope(sink=recorder, phase="research", unit_id="query_1"):
        diagnostic(
            "tool_requested",
            arguments={"query": "transformer IDS", "api_key": "private"},
            note="Bearer token-value configured-secret https://user:password@host/?token=secret",
            content={"prompt": "not default"},
        )
    recorder.close()
    item = records(path)[0]
    assert item["phase"] == "research" and item["unit_id"] == "query_1"
    assert item["arguments"]["query"] == "transformer IDS"
    assert item["arguments"]["api_key"] == "[REDACTED]"
    assert "content" not in item
    assert "configured-secret" not in path.read_text()
    assert "token-value" not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    with pytest.raises(output.UsageError):
        TraceRecorder(path)
    assert path.read_bytes() == before
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(output.UsageError):
        TraceRecorder(link)


async def test_async_scopes_are_independent_and_sink_failure_is_safe():
    seen = []

    async def child(section):
        with diagnostic_scope(section_id=section):
            await asyncio.sleep(0)
            diagnostic("test")

    with diagnostic_scope(sink=lambda item, **kw: seen.append(item), phase="write"):
        await asyncio.gather(child("section_1"), child("section_2"))
    assert {item["section_id"] for item in seen} == {"section_1", "section_2"}

    def broken(*args, **kwargs):
        raise OSError("disk error")

    with diagnostic_scope(sink=broken):
        diagnostic("test")


async def test_schema_reason_and_explicit_raw_response(tmp_path):
    class Answer(BaseModel):
        value: int

        @model_validator(mode="after")
        def reject(self):
            raise ValueError("Unverified hypothesis cannot become factual prose")

    class Model:
        calls = 0

        async def complete(self, prompt):
            self.calls += 1
            return '{"value":1}'

    path = tmp_path / "model.jsonl"
    recorder = TraceRecorder(path, content=True)
    model = Model()
    with (
        diagnostic_scope(sink=recorder, phase="write", section_id="section_2"),
        pytest.raises(AdapterError) as failure,
    ):
        await complete(model, "controlled", Answer, operation="draft_chapter")

    recorder.close()
    assert failure.value.code == "model_output_invalid" and model.calls == 2
    errors = records(path)
    assert len(errors) == 2
    assert errors[0]["content"]["response"] == '{"value":1}'
    assert "Unverified hypothesis" in errors[0]["errors"][0]["msg"]
    assert "input" not in errors[0]["errors"][0]
    assert errors[0]["section_id"] == "section_2"


async def test_content_flag_requires_file():
    args = build_parser().parse_args(["phase", "plan", "--state", "unused", "--trace-content"])
    with pytest.raises(output.UsageError, match="requires --trace"):
        await execute(args)


def test_result_summary_excludes_snippets_and_is_total():
    result = result_summary(
        "search", [{"title": "Paper", "url": "https://example.com", "snippet": "full body"}]
    )
    assert result[0]["url"] == "https://example.com"
    assert "snippet" not in result[0]
    assert result_summary("search", {"unexpected": True}) == {"result_type": "dict"}


def test_write_failure_does_not_escape(tmp_path, capsys):
    recorder = TraceRecorder(tmp_path / "failed.jsonl")
    recorder.file.close()
    recorder({"event": "test"})
    recorder.close()
    assert recorder.error == "trace_write_failed"
    assert "trace_incomplete" in capsys.readouterr().err


async def test_real_cli_process_exports_query_without_changing_stdout(tmp_path):
    import sys
    from pathlib import Path

    from tests.unit.test_state import initial_state

    state = initial_state()
    path = tmp_path / "state.json"
    path.write_text(state.model_dump_json())

    async def invoke(phase, trace):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "cli",
            "phase",
            phase,
            "--state",
            str(path),
            "--json",
            "--trace",
            str(trace),
            cwd=Path(__file__).resolve().parents[2],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        # Native gRPC in the parent can write fork diagnostics before exec.
        # The CLI contract isolates JSON on stdout; stderr may contain logs.
        assert process.returncode == 0, stderr.decode()
        assert b"Traceback" not in stderr and b"trace_incomplete" not in stderr
        return json.loads(stdout)

    planned = await invoke("plan", tmp_path / "plan.jsonl")
    path.write_text(json.dumps(planned["state"] | {"phase": "research"}))
    research = await invoke("research", tmp_path / "research.jsonl")
    assert research["dependency_mode"] == "fake"
    trace = records(tmp_path / "research.jsonl")
    requested = [
        item for item in trace if item["event"] == "tool_requested" and item["tool"] == "search"
    ]
    assert requested and all(item["arguments"]["query"] for item in requested)
    assert all(
        item["phase"] == "research" and item["unit_id"] and item["section_ids"]
        for item in requested
    )
    assert trace[-1]["event"] == "trace_finished"
    assert not any("content" in item for item in trace)
