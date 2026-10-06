"""Unit tests for the CLI output contract + seed determinism (T001)."""

import json

import pytest

from cli import output
from cli.__main__ import main
from cli.commands import doctor
from infrastructure.fake import FakeLLM


@pytest.mark.asyncio
async def test_seeded_fake_llm_is_deterministic() -> None:
    a = await FakeLLM(seed=42).complete("generate section_plans")
    b = await FakeLLM(seed=42).complete("generate section_plans")
    assert a == b


@pytest.mark.asyncio
async def test_seeded_fake_llm_varies_by_seed() -> None:
    a = await FakeLLM(seed=42).complete("generate section_plans")
    c = await FakeLLM(seed=43).complete("generate section_plans")
    assert a != c


def test_emit_json_is_a_single_parseable_object(capsys) -> None:
    output.emit_json("ok", {"final_report": {"sections": {}}})
    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed == {"status": "ok", "error": None, "final_report": {"sections": {}}}


def test_log_goes_to_stderr(capsys) -> None:
    output.log("hello")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hello" in captured.err


def test_foreign_exception_does_not_leak_sdk_text_and_has_json_error(monkeypatch, capsys):
    async def broken(args):
        raise RuntimeError("secret-key-and-private-prompt")

    monkeypatch.setattr(doctor, "run", broken)
    assert main(["doctor", "--json"]) == 1
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["error"]["code"] == "execution_failed"
    assert "secret-key-and-private-prompt" not in captured.out + captured.err
