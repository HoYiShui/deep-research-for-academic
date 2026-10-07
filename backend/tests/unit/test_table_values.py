"""Whole-cell boundary, including the recorded real MinerU failure shape."""

import json
from pathlib import Path

import pytest

from domain.research.agents.table_values import cell_values


def test_recorded_real_pdf_table_does_not_turn_split_exponent_into_integer():
    path = (
        Path(__file__).resolve().parents[3]
        / "specs/004-backend-mono-alignment/evidence/t043-pdf-local-text-real.json"
    )
    record = json.loads(path.read_text())
    block = next(
        b
        for b in record["document"]["blocks"]
        if b["type"] == "table" and b["location"]["page_start"] == 8
    )
    values = cell_values(block["content"])
    assert "3.3 ·" in values and "10^18" in values
    assert "3.3" not in values and "1018" not in values
    assert "28.4" in values and "41.8" in values


@pytest.mark.parametrize(
    "content",
    [
        "<table><tr><td>1<td>2</td></tr></table>",
        "<table><td>1<script>hidden</script></td></table>",
        "<table><td>1<table><td>2</td></table></td></table>",
        "table image only",
    ],
)
def test_unsupported_or_ambiguous_cell_structure_rejects_instead_of_guessing(content):
    with pytest.raises(ValueError):
        cell_values(content)
