"""Unit tests for the slice command (T004)."""

import json

from cli.__main__ import main


def test_slice_review_fake(tmp_path, capsys) -> None:
    state = {"session_id": "s1", "phase": "review"}
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    code = main(["slice", "review", "--input", str(path), "--fake", "--json"])
    out = capsys.readouterr().out
    assert code == 0
    assert '"phase": "review"' in out


def test_slice_rejects_unknown_phase() -> None:
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["slice", "bogus", "--input", "x.json", "--fake", "--json"])
    assert exc.value.code == 2
