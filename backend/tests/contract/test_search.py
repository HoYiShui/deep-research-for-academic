"""Contract tests for SearchPort adapters (parsing logic, no network)."""

from domain.ports import SearchResult
from infrastructure.search.arxiv import _parse


def test_arxiv_parse_extracts_title_summary_url() -> None:
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Test Paper</title>
        <summary>A summary.</summary>
        <id>http://arxiv.org/abs/0000.00000</id>
      </entry>
    </feed>"""
    results = _parse(xml)
    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "Test Paper"
    assert results[0].source_id == "http://arxiv.org/abs/0000.00000"
