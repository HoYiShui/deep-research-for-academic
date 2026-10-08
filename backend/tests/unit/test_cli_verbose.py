"""Verbose metadata must not leak a private prompt or provider response."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cli.container import _VerboseLLM
from cli.run_real import log_progress
from domain.research.ids import stable_id
from domain.research.run_events import ProgressFrame


async def test_verbose_does_not_print_content(capsys):
    class Model:
        async def complete(self, prompt):
            assert prompt == "secret-private-research-input"
            return "secret-private-research-output"

    result = await _VerboseLLM(Model(), True).complete("secret-private-research-input")
    assert result == "secret-private-research-output"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret-private" not in captured.err
    assert "input_chars=" in captured.err and "output_chars=" in captured.err


def progress():
    return ProgressFrame(
        event_id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        timestamp=datetime.now(UTC),
        checkpoint_seq=2,
        event="progress",
        phase="research",
        section_id="section_1",
        stage="query_started",
        unit_id=stable_id("unit", "controlled"),
        completed_units=0,
        total_units=10,
        message="private-message-canary",
        results={"query": "private-result-canary"},
        chart={"value": "private-chart-canary"},
    )


def test_run_progress_logs_only_safe_metadata(capsys):
    event = progress()
    log_progress(event, event.run_id)
    started = capsys.readouterr()
    assert started.out == "" and "canary" not in started.err
    assert "stage=query_started" in started.err and "persistence=uncommitted" in started.err
    log_progress(event.model_copy(update={"stage": "query_completed"}), event.run_id)
    finished = capsys.readouterr()
    assert "persistence=pg_checkpoint" in finished.err and "canary" not in finished.err


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", uuid4()),
        ("unit_id", "private-unit-canary"),
        ("phase", "private-phase-canary"),
        ("stage", "private-stage-canary"),
    ],
)
def test_run_progress_rejects_other_identity_or_unvalidated_metadata(capsys, field, value):
    event = progress()
    log_progress(event.model_copy(update={field: value}), event.run_id)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
