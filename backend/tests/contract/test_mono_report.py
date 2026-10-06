"""Deterministic report contract, including refusal to hide unsafe assertions."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest

from application.errors import AppError
from application.report_serializer import serialize_report
from domain.research.facts import DraftSection
from domain.research.ids import canonical_hash
from domain.research.reporting import (
    TASK_TITLES,
    artifact_files,
    draft_content,
    statement_anchor,
    validate_publication,
)
from domain.research.state import PipelineState
from tests.report_fixtures import insufficient_review

NOW = datetime(2026, 10, 6, tzinfo=UTC)
REPORT_ID = uuid4()


def report(state):
    return serialize_report(state, report_id=REPORT_ID, created_at=NOW)


def changed(state, **values):
    return PipelineState.model_validate(state.model_dump() | values)


def supported():
    state = insufficient_review()
    data = state.model_dump()
    original = "This controlled original paragraph states a bounded method fact."
    source = {
        "source_id": "source-real-fixture",
        "source_type": "web",
        "title": "Registered source fixture",
        "authors_or_publisher": ["Fixture publisher"],
        "published_at": None,
        "version": "v1",
        "canonical_url": "https://example.org/original",
        "provenance": [
            {
                "retrieved_at": NOW,
                "retrieved_via": "web",
                "original_ref": "https://example.org/original",
                "document_version_id": None,
                "upstream_source_id": None,
            }
        ],
        "source_tier": "official",
        "content_object_key": "controlled/original",
        "content_hash": sha256(original.encode()).hexdigest(),
        "data_classification": "public",
    }
    data["sources"] = {source["source_id"]: source}
    data["evidence"] = {
        "e1": {
            "evidence_id": "e1",
            "source_id": source["source_id"],
            "evidence_type": "method",
            "location": {"page_start": 7, "table": "Table 4"},
            "quote_or_raw_content": original,
            "extraction_method": "html_paragraph",
            "content_hash": sha256(original.encode()).hexdigest(),
        }
    }
    data["claims"]["c1"] = {
        "claim_id": "c1",
        "spec_ids": ["spec-1"],
        "text": original,
        "claim_type": "factual",
        "conditions": {"scope": "controlled"},
        "status": "supported",
        "status_reason": "Controlled original relation; not a live research test",
    }
    data["claim_evidence_links"] = [
        {
            "claim_id": "c1",
            "evidence_id": "e1",
            "relation": "supports",
            "rationale": "Controlled exact paragraph registration",
        }
    ]
    data["section_coverage"]["section_1"] |= {
        "claim_ids": ["c1"],
        "evidence_ids": ["e1"],
        "covered_claim_ids": ["c1"],
        "gaps": [],
    }
    data["draft_sections"]["section_1"]["statements"] = [
        {"statement_id": "statement-1", "text": original, "kind": "factual"}
    ]
    data["draft_sections"]["section_1"]["content"] = original
    data["draft_claim_bindings"] = [
        {
            "draft_version": 1,
            "section_id": "section_1",
            "statement_id": "statement-1",
            "claim_ids": ["c1"],
            "cited_evidence_ids": ["e1"],
            "artifact_ids": [],
        }
    ]
    return PipelineState.model_validate(data)


def analyzed():
    data = supported().model_dump()
    data["section_plans"][0]["analysis_requirements"] = [
        {
            "requirement_id": "req-stat",
            "operation": "statistic",
            "claim_spec_ids": ["spec-1"],
            "required_context_fields": ["dataset", "protocol"],
            "parameters": {"kind": "mean", "group_by": []},
        }
    ]
    data["quantitative_observations"] = {
        "o1": {
            "observation_id": "o1",
            "evidence_id": "e1",
            "kind": "benchmark_result",
            "row_key": {"method": "fixture"},
            "column_key": {"metric": "score"},
            "raw_value": "1",
            "value": "1",
            "uncertainty": None,
            "statistic": "reported",
            "context": {"dataset": "D", "protocol": "P"},
            "unit": "score",
        }
    }
    data["comparable_metrics"] = {
        "m1": {
            "comparable_metric_id": "m1",
            "observation_ids": ["o1"],
            "metric_definition": "controlled score",
            "evaluated_method": "fixture",
            "evaluation_context": {"dataset": "D", "protocol": "P"},
            "value": "1",
            "unit": "score",
            "normalization_basis": "controlled same reported unit",
            "missing_context_fields": [],
        }
    }
    data["comparison_sets"] = {
        "g1": {
            "comparison_set_id": "g1",
            "section_id": "section_1",
            "requirement_id": "req-stat",
            "metric_ids": ["m1"],
            "required_context_fields": ["dataset", "protocol"],
            "comparability": "compatible",
            "reasons": ["Controlled same-condition fixture"],
        }
    }
    data["analysis_artifacts"] = {
        "a1": {
            "artifact_id": "a1",
            "section_id": "section_1",
            "input_metric_ids": ["m1"],
            "input_evidence_ids": ["e1"],
            "comparison_set_id": "g1",
            "operation": "statistic",
            "code_or_recipe": "Controlled recipe fixture, not a real sandbox execution",
            "template_version": data["run_metadata"]["config"]["versions"]["template_versions"][
                "statistic"
            ],
            "output": {
                "groups": [
                    {"key": {}, "metric_ids": ["m1"], "count": 1, "value": "1", "unit": "score"}
                ]
            },
            "object_keys": ["analysis/run/artifact/result.png"],
            "execution_status": "completed",
            "failure": None,
        }
    }
    data["draft_claim_bindings"][0]["artifact_ids"] = ["a1"]
    return PipelineState.model_validate(data)


@pytest.mark.parametrize("task", list(TASK_TITLES))
def test_three_tasks_render_fixed_structure_payload_risks_and_empty_references(task):
    state = insufficient_review(task=task)
    value = report(state)
    assert value.review_verdict == "needs_more_work" and value.risks
    assert value.references == [] and "无可核验引用。" in value.markdown
    headings = [line for line in value.markdown.splitlines() if line.startswith("## ")]
    assert (
        len(headings) == 7
        and headings[0] == "## 0. Research Brief"
        and headings[-1] == "## References"
    )
    assert headings[3] == f"## 3. {TASK_TITLES[task]}"
    assert value.sections == state.draft_sections and value.bindings == state.draft_claim_bindings
    assert report(state) == value
    validate_publication(state, value)
    for section in state.draft_sections.values():
        for statement in section.statements:
            assert value.markdown.count(f'id="{statement_anchor(statement.statement_id)}"') == 1
    if task == "evaluation_design":
        assert "不能支持的结论" in value.markdown and "不能声称优于其他方法" in value.markdown


def test_only_actually_cited_registered_sources_get_stable_reference_numbers_and_locations():
    state = supported()
    data = state.model_dump()
    data["sources"]["unused"] = data["sources"]["source-real-fixture"] | {
        "source_id": "unused",
        "title": "Do not fabricate a bibliography entry",
    }
    value = report(PipelineState.model_validate(data))
    assert len(value.references) == 1 and value.references[0].reference_id == "R1"
    assert value.references[0].evidence_ids == ["e1"] and "R1，p.7/Table 4" in value.markdown
    assert "Do not fabricate" not in value.markdown


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "empty",
        "unrelated",
        "open",
        "hypothesis",
        "snippet",
        "unfetched",
        "no_provenance",
        "private",
    ],
)
def test_factual_bindings_require_original_related_qualified_evidence(mode):
    data = supported().model_dump()
    if mode == "missing":
        data["draft_claim_bindings"] = []
    elif mode == "empty":
        data["draft_claim_bindings"][0]["cited_evidence_ids"] = []
    elif mode == "unrelated":
        data["evidence"]["e2"] = data["evidence"]["e1"] | {"evidence_id": "e2"}
        data["draft_claim_bindings"][0]["cited_evidence_ids"] = ["e2"]
    elif mode == "open":
        data["claims"]["c1"]["status"] = "open"
        data["section_coverage"]["section_1"]["gaps"] = (
            insufficient_review().section_coverage["section_1"].gaps
        )
    elif mode == "hypothesis":
        data["claims"]["c1"]["claim_type"] = "hypothesis"
    elif mode == "snippet":
        data["evidence"]["e1"]["extraction_method"] = "search_snippet"
    elif mode == "unfetched":
        data["sources"]["source-real-fixture"]["content_object_key"] = None
    elif mode == "no_provenance":
        data["sources"]["source-real-fixture"]["provenance"] = []
    else:
        data["sources"]["source-real-fixture"]["data_classification"] = "private"
    with pytest.raises(AppError, match="delivery checks"):
        report(PipelineState.model_validate(data))


@pytest.mark.parametrize(
    "field",
    [
        "content",
        "payload",
        "row_ids",
        "summary",
        "row_claim",
        "row_assertion",
        "duplicate_statement",
    ],
)
def test_unregistered_prose_payload_assertions_and_ids_are_rejected(field):
    data = insufficient_review().model_dump()
    section = data["draft_sections"]["section_3"]
    if field == "content":
        section["content"] += "\n\nUnregistered result: our model beats every baseline."
    elif field == "payload":
        section["task_payload"] = None
    elif field == "row_ids":
        section["task_payload"]["protocol_rows"][0]["statement_ids"] = ["unknown"]
    elif field == "summary":
        section["task_payload"]["failure_modes"] = ["Unreviewed new assertion"]
    elif field == "row_claim":
        section["task_payload"]["protocol_rows"][0]["claim_id"] = "unknown"
    elif field == "row_assertion":
        section["task_payload"]["protocol_rows"][0]["supported_conclusions"] = (
            "Unreviewed proven accuracy gain"
        )
    else:
        section["statements"][0]["statement_id"] = "statement-1"
    with pytest.raises(AppError):
        report(PipelineState.model_validate(data))


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,evil",
        "file:///etc/passwd",
        "https://user:password@example.org/",
        "https://example.org/\nscript",
        "//example.org/path",
    ],
)
def test_unsafe_citation_protocols_or_credentials_cannot_publish(url):
    data = supported().model_dump()
    data["sources"]["source-real-fixture"]["canonical_url"] = url
    with pytest.raises(AppError):
        report(PipelineState.model_validate(data))


def test_markdown_html_and_anchor_injection_is_literal_not_active_content():
    state = insufficient_review()
    data = state.model_dump()
    text = "<script>alert(1)</script> [click](javascript:alert(1)) | x\n# forged heading"
    section = data["draft_sections"]["section_1"]
    section["statements"][0] |= {"statement_id": 'x" onmouseover="alert(1)', "text": text}
    section["content"] = draft_content(DraftSection.model_validate(section))
    value = report(PipelineState.model_validate(data))
    assert "<script>" not in value.markdown and "&lt;script&gt;" in value.markdown
    assert "[click](javascript:" not in value.markdown and "\n# forged" not in value.markdown
    assert 'id="x"' not in value.markdown


@pytest.mark.parametrize(
    "deliverable",
    [
        "至少3个候选研究问题",
        "三个候选问题",
        "至少十三个候选研究问题",
        "exactly three candidate research questions",
        "at least 2 candidate questions",
        "一百个候选问题",
    ],
)
def test_candidate_count_follows_explicit_brief_and_cannot_skip_required_task_module(deliverable):
    state = insufficient_review(task="idea_exploration")
    data = state.model_dump()
    data["research_brief"]["deliverable"] = deliverable
    data["brief_hash"] = canonical_hash(data["research_brief"])
    with pytest.raises(AppError):
        report(PipelineState.model_validate(data))


@pytest.mark.parametrize(
    "deliverable", ["至多三个候选研究问题", "至少一个候选问题", "exactly one candidate question"]
)
def test_explicit_candidate_upper_bound_or_minimum_is_not_misread_as_exact(deliverable):
    data = insufficient_review(task="idea_exploration").model_dump()
    data["research_brief"]["deliverable"] = deliverable
    data["brief_hash"] = canonical_hash(data["research_brief"])
    assert (
        report(PipelineState.model_validate(data)).sections["section_3"].task_payload.task_type
        == "idea_exploration"
    )


def test_approved_cannot_upgrade_an_insufficient_report_and_no_free_markdown_override():
    state = insufficient_review()
    with pytest.raises(AppError):
        report(changed(state, review_verdict="approved"))
    value = report(state)
    with pytest.raises(ValueError):
        validate_publication(
            state,
            type(value).model_validate(
                value.model_dump() | {"markdown": value.markdown + "New unchecked conclusion"}
            ),
        )


def test_stale_draft_and_dangling_fact_ids_are_refused_even_after_mutating_nested_state():
    for mutate in [
        lambda state: state.claims.clear(),
        lambda state: object.__setattr__(state.draft_sections["section_1"], "draft_version", 2),
    ]:
        state = supported()
        mutate(state)
        with pytest.raises(AppError):
            report(state)


def test_attachment_basename_and_extension_whitelist_rejects_traversal_and_active_files():
    class FixtureArtifact:
        operation = "statistic"

        def __init__(self):
            self.output = {}
            self.object_keys = []

    artifact = FixtureArtifact()
    for key in [
        "analysis/../plot.png",
        "/plot.png",
        "analysis/evil.html",
        "analysis/plot.svg",
        "analysis/back\\slash.png",
    ]:
        artifact.object_keys = [key]
        with pytest.raises(ValueError):
            artifact_files(artifact)
    artifact.object_keys = ["analysis/run/artifact/result.png"]
    assert artifact_files(artifact) == ["result.png"]


def test_attachment_links_use_authorized_api_path_not_object_keys_or_public_minio_urls():
    state = analyzed()
    value = report(state)
    assert f"/research/{state.session_id}/artifacts/a1/files/result.png" in value.markdown
    assert "analysis/run/artifact" not in value.markdown
    assert "minio" not in value.markdown.lower()


@pytest.mark.parametrize(
    "fault",
    [
        "failed",
        "incompatible",
        "wrong_template",
        "empty_inputs",
        "unknown_context",
        "missing_judgment",
        "lost_evidence",
        "unplanned",
    ],
)
def test_unqualified_analysis_never_becomes_a_cited_computation(fault):
    data = analyzed().model_dump()
    if fault == "failed":
        data["analysis_artifacts"]["a1"].update(execution_status="failed", output={})
    elif fault == "incompatible":
        data["comparison_sets"]["g1"]["comparability"] = "incompatible"
    elif fault == "wrong_template":
        data["analysis_artifacts"]["a1"]["template_version"] = "unfrozen-version"
    elif fault == "empty_inputs":
        data["analysis_artifacts"]["a1"]["input_metric_ids"] = []
    elif fault == "unknown_context":
        data["comparable_metrics"]["m1"]["evaluation_context"]["dataset"] = "unknown"
    elif fault == "missing_judgment":
        data["analysis_artifacts"] = {}
        data["comparison_sets"] = {}
        data["draft_claim_bindings"][0]["artifact_ids"] = []
    elif fault == "lost_evidence":
        data["analysis_artifacts"]["a1"]["input_evidence_ids"] = []
    else:
        data["section_plans"][0]["analysis_requirements"] = []
    with pytest.raises(AppError):
        report(PipelineState.model_validate(data))


def test_source_tier_requirements_are_not_overridden_by_an_approved_model_flag():
    data = supported().model_dump()
    data["section_plans"][0]["claim_specs"][0]["required_source_tiers"] = ["peer_reviewed"]
    with pytest.raises(AppError):
        report(PipelineState.model_validate(data))


def test_terminal_contraction_keeps_omitted_required_analysis_as_explicit_risk():
    data = analyzed().model_dump()
    data["analysis_artifacts"] = {}
    data["comparison_sets"] = {}
    data["draft_claim_bindings"][0]["artifact_ids"] = []
    data["run_metadata"]["stop_reason"] = "budget_exhausted"
    value = report(PipelineState.model_validate(data))
    assert value.review_verdict == "needs_more_work"
    assert any("req-stat" in item.description for item in value.risks)


def test_bounded_report_fails_instead_of_silently_discarding_assertions(monkeypatch):
    monkeypatch.setattr("domain.research.reporting.MAX_REPORT_BYTES", 10)
    with pytest.raises(AppError):
        report(insufficient_review())
