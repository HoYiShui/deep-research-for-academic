"""Formal plan/research units with PG/MinIO/HTTP/parser and controlled model."""

import asyncio
import json

import pytest

from application.errors import AppError
from application.fetch_tools import FetchBinding
from application.phase_executor import PhaseExecutor
from application.phase_workers import plan_worker, research_worker
from application.settings import Settings
from domain.model_completion import ModelCompletion
from infrastructure.fetch.document import HTTPDocumentFetch
from infrastructure.parser.html import HTML_PARSER_VERSION, HTMLDocumentParser
from tests.contract.test_document_parser import HTML
from tests.contract.test_fetch import downloader, response
from tests.integration.test_mono_document_content import content_store as shared_content_store
from tests.integration.test_mono_run_driver import world
from tests.integration.test_mono_search_tools import Provider, binding

content_store = shared_content_store


@pytest.mark.parametrize("empty", [False, True])
async def test_formal_research_commits_each_query_with_fact_links_and_shared_claim_specs(
    pg_database, object_cache, content_store, monkeypatch, empty
):
    monkeypatch.setattr(
        "tests.integration.test_mono_transactions.Settings",
        lambda: Settings(parser_version=HTML_PARSER_VERSION),
    )
    pool, store, user, commit, claimed, model, units, _, _, _, make_driver = await world(
        pg_database, object_cache
    )
    original_model = model.complete_metered
    extracted = []

    async def complete(prompt):
        if "<original_context>" not in prompt:
            return await original_model(prompt)
        context = json.loads(
            prompt.split("<original_context>\n", 1)[1].split("\n</original_context>", 1)[0]
        )
        plan, blocks = context["section_plan"], context["original_blocks"]
        block = next(block for block in blocks if block["type"] == "table")
        quote = "\n".join(
            [block["content"], *([block["caption"]] if block["caption"] else []), *block["notes"]]
        )
        output = {
            "evidence": [
                {
                    "key": "table",
                    "block_index": block["block_index"],
                    "quote": quote,
                    "evidence_type": "result_table",
                }
            ],
            "claims": [
                {
                    "subject": "A",
                    "predicate": "has",
                    "object": "observed score",
                    "spec_ids": [spec["spec_id"] for spec in plan["claim_specs"]],
                    "claim_type": "factual",
                    "conditions": {},
                    "relations": [
                        {
                            "evidence_key": "table",
                            "relation": "supports",
                            "rationale": "Original table cell",
                        }
                    ],
                }
            ],
            "observations": [
                {
                    "evidence_key": "table",
                    "kind": "benchmark_result",
                    "row_key": {"method": "A"},
                    "column_key": {"metric": "Score"},
                    "raw_value": "95.0",
                    "value": "95.0",
                    "uncertainty": None,
                    "statistic": "reported",
                    "context": {},
                    "unit": "%",
                }
            ],
        }
        extracted.append(plan["section_id"])
        return ModelCompletion(
            response_id="controlled-research",
            model=model.model,
            text=json.dumps(output),
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=100,
        )

    model.complete_metered = complete
    transport, network, _ = downloader([response(HTML.encode(), media="text/html")])
    parser = HTMLDocumentParser(content_store)
    driver = make_driver(
        search=binding(Provider("paper", empty=True), Provider("web", empty=empty)),
        fetch=FetchBinding(
            lambda run_id, config: HTTPDocumentFetch(
                content_store, parser, config, run_id, downloader=transport
            )
        ),
    )
    driver.executor = PhaseExecutor({"plan": plan_worker, "research": research_worker})
    try:
        # Analyze is intentionally absent: no fake successful full report.
        with pytest.raises(AppError, match="service_not_ready"):
            await driver.execute(claimed, asyncio.Event())
        latest = await store.research.load_latest_checkpoint(user.user_id, commit.run.run_id)
        assert latest.phase == "analyze" and latest.seq == 14
        assert len([unit for unit in units if unit.checkpoint.phase == "research"]) == 10
        assert len(latest.state.section_coverage) == 5
        assert await pool.fetchval("SELECT count(*) FROM reports") == 0
        if empty:
            assert extracted == network.connected == []
            assert not latest.state.evidence
            assert len(latest.state.claims) == 1
            assert all(
                claim.claim_type == "hypothesis" and claim.status == "insufficient"
                for claim in latest.state.claims.values()
            )
            assert all(coverage.gaps for coverage in latest.state.section_coverage.values())
            assert (
                await pool.fetchval("SELECT count(*) FROM tool_call_attempts WHERE tool='fetch'")
                == 0
            )
        else:
            assert len(extracted) == 5 and len(network.connected) == 1
            assert (
                len(latest.state.sources)
                == len(latest.state.evidence)
                == len(latest.state.claims)
                == len(latest.state.quantitative_observations)
                == 1
            )
            claim = next(iter(latest.state.claims.values()))
            assert claim.spec_ids == [f"spec-{index}" for index in range(1, 6)]
            assert claim.status == "supported"
            observation = next(iter(latest.state.quantitative_observations.values()))
            assert (
                observation.value == "95.0" and "Score (%)" in observation.context["source_block"]
            )
            assert observation.evidence_id in latest.state.evidence
            assert all(
                coverage.covered_claim_ids == [claim.claim_id] and not coverage.gaps
                for coverage in latest.state.section_coverage.values()
            )
            assert (
                await pool.fetchval("SELECT count(*) FROM tool_call_attempts WHERE tool='fetch'")
                == 1
            )
    finally:
        await parser.close()
