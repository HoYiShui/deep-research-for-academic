"""Unit tests for scout quantitative-observation extraction (T045)."""

import pytest

from domain.research.agents import scout
from infrastructure.fake import FakeLLM


@pytest.mark.asyncio
async def test_extract_observations_builds_id_keyed_rows() -> None:
    llm = FakeLLM(
        response='{"observations": [{"row_key": "method A", "column_key": "ACC", '
        '"value": "0.94", "uncertainty": "+/-0.01", "statistic": "mean", '
        '"evidence_id": "ev-1"}]}'
    )
    evidence = {
        "ev-1": {
            "evidence_id": "ev-1",
            "source_id": "s1",
            "location": "p.7 Table 4",
            "quote_or_raw_content": "ACC 0.94",
        }
    }
    observations = await scout._extract_observations(llm, evidence)
    assert len(observations) == 1
    obs = next(iter(observations.values()))
    assert obs["row_key"] == "method A"
    assert obs["column_key"] == "ACC"
    assert obs["value"] == "0.94"
    assert obs["evidence_id"] == "ev-1"
    assert obs["observation_id"].startswith("obs-")


@pytest.mark.asyncio
async def test_extract_observations_drops_unknown_evidence() -> None:
    llm = FakeLLM(
        response='{"observations": [{"row_key": "x", "column_key": "y", '
        '"value": "1", "evidence_id": "ev-unknown"}]}'
    )
    evidence = {"ev-1": {"evidence_id": "ev-1"}}
    assert await scout._extract_observations(llm, evidence) == {}


@pytest.mark.asyncio
async def test_extract_observations_empty_evidence() -> None:
    assert await scout._extract_observations(FakeLLM(), {}) == {}
