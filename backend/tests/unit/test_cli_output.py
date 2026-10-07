"""Unit tests for the CLI output contract + seed determinism (T001)."""

import json
import subprocess
import sys
from pathlib import Path

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


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--json"],
        ["phase", "private-phase-canary", "--state", "x.json", "--json"],
        ["doctor", "--scope", "private-scope-canary", "--json"],
        ["doctor", "--unknown-private-canary", "--json"],
        ["--json"],
    ],
)
def test_parser_errors_honor_json_and_do_not_echo_untrusted_values(argv, capsys):
    assert main(argv) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "usage_error"
    assert result["error"]["code"] == "validation_error"
    assert result["error"]["request_id"]
    assert not result["error"]["retryable"]
    assert "canary" not in captured.out + captured.err
    assert "usage:" not in captured.err


def test_help_remains_a_successful_non_execution_action(capsys):
    with pytest.raises(SystemExit) as exited:
        main(["phase", "--help"])
    assert exited.value.code == 0
    assert "--state" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--json"],
        ["phase", "private-phase-canary", "--state", "x.json", "--json"],
    ],
)
def test_actual_cli_process_exits_two_with_one_json_object(argv):
    result = subprocess.run(
        [sys.executable, "-m", "cli", *argv],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "usage_error"
    assert "Traceback" not in result.stderr
    assert "canary" not in result.stdout + result.stderr


def test_end_of_options_positional_value_is_not_json_flag(capsys):
    assert main(["private-command-canary", "--", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out.startswith("status: usage_error")
    assert "canary" not in captured.out + captured.err
