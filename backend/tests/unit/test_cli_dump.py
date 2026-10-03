"""Unit tests for the dump command (T005)."""

from unittest.mock import AsyncMock, patch

from cli.__main__ import main


def test_dump_session_not_found(capsys) -> None:
    with patch("cli.commands.dump.PostgresStateStore") as MockStore:
        MockStore.return_value.load_latest_snapshot = AsyncMock(return_value=None)
        code = main(["dump", "s1", "--json"])
        out = capsys.readouterr().out
    assert code == 1
    assert '"status": "failed"' in out
