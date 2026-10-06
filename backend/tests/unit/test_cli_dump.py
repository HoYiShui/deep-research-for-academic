"""Unit tests for the dump command (T005)."""

import json

from cli.__main__ import main


def test_dump_invalid_id_is_json_usage_error(capsys) -> None:
    code = main(["dump", "s1", "--json"])
    assert code == 2
    body = json.loads(capsys.readouterr().out)
    assert body["status"] == "usage_error"
    assert body["error"]["code"] == "validation_error"
