"""Unit tests for writer.write_report."""

from domain.research.agents.writer import write_report


def test_bindings_cite_evidence_ids() -> None:
    result = write_report(
        [{"section_id": "s1"}],
        {"e1": {"evidence_id": "e1", "source_id": "s1"}},
    )
    assert result["draft_sections"]["s1"]["section_id"] == "s1"
    assert result["draft_claim_bindings"][0]["cited_evidence_ids"] == ["e1"]


def test_draft_sections_are_id_keyed() -> None:
    result = write_report(
        [{"section_id": "s1"}, {"section_id": "s2"}],
        {},
    )
    assert set(result["draft_sections"]) == {"s1", "s2"}
