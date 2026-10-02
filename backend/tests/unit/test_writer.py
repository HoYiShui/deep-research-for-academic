"""Unit tests for writer.write_report."""

from domain.research.agents.writer import write_report


def test_bindings_cite_evidence_ids() -> None:
    result = write_report(
        [{"section_id": "s1"}],
        [{"source_id": "e1", "source_type": "paper", "location": "p.1", "quote": "x"}],
    )
    assert result["draft_sections"][0]["section_id"] == "s1"
    assert result["draft_claim_bindings"][0]["cited_evidence_ids"] == ["e1"]
