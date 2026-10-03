"""Unit tests for the ingest + search commands (T006)."""

from unittest.mock import AsyncMock, patch

from cli.__main__ import main


def test_ingest_ok(capsys) -> None:
    kb = type("KB", (), {})()
    kb.ingest = AsyncMock(return_value={"document_id": "d1", "status": "done"})
    container = type("C", (), {"knowledge_base": kb})()
    with patch("cli.commands.ingest.container.build_container", return_value=container):
        code = main(["ingest", "paper.pdf", "--json"])
        out = capsys.readouterr().out
    assert code == 0
    assert '"status": "ok"' in out


def test_search_ok(capsys) -> None:
    from domain.ports import Chunk

    retrieval = type("R", (), {})()
    retrieval.retrieve = AsyncMock(return_value=[Chunk("c1", "hello", 0.9, {})])
    container = type("C", (), {"retrieval": retrieval})()
    with patch("cli.commands.search.container.build_container", return_value=container):
        code = main(["search", "query", "--json"])
        out = capsys.readouterr().out
    assert code == 0
    assert '"chunks"' in out
