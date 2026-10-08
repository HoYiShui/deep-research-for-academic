"""Canonical drafting/review with controlled model content, not research quality."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from application.phase_units import plan_units
from application.phase_workers import analyze_worker, review_worker, write_worker
from cli.phase_tools import DebugTools
from domain.ports import AdapterError
from domain.research.agents.critic import review_draft
from domain.research.agents.data_analyst import assess_requirement
from domain.research.agents.writer import ChapterDraft, materialize_chapter
from domain.research.phase_contracts import PhaseInput, merge_phase_result
from domain.research.reporting import build_report
from domain.research.state import PipelineState
from tests.report_fixtures import insufficient_review


def state(task="evaluation_design"):
    initial = insufficient_review(task=task)
    data = initial.model_dump()
    coverage = data["section_coverage"]["section_3"]
    coverage["claim_ids"] = ["c-proposal"]
    data.update(
        phase="write",
        draft_sections={},
        draft_version=0,
        draft_claim_bindings=[],
        reviewed_draft_version=None,
        review_verdict=None,
    )
    return PipelineState.model_validate(data)


def context(value, invoke):
    unit = plan_units(value)[0]

    async def stopping():
        return False

    return SimpleNamespace(
        unit=unit,
        unit_id=unit.unit_id,
        config=value.run_metadata.config,
        invoke=invoke,
        cancel_check=stopping,
    )


@pytest.mark.parametrize(
    "task", ["idea_exploration", "method_differentiation", "evaluation_design"]
)
async def test_five_chapter_writer_actual_review_and_serializer(task):
    initial = state(task)
    fake = DebugTools(initial, fake=True)
    prompts = []

    async def invoke(tool, arguments):
        assert tool == "llm"
        prompts.append(arguments["prompt"])
        if arguments["phase"] == "write":
            prompt_context = json.loads(
                arguments["prompt"]
                .split("<chapter_context>\n", 1)[1]
                .split("\n</chapter_context>", 1)[0]
            )
            assert prompt_context["claim_evidence_links"] == []
            assert prompt_context["comparable_metrics"] == {}
            assert prompt_context["comparison_sets"] == {}
            schema = json.loads(
                arguments["prompt"]
                .split("应用使用的输出对象模型：\n", 1)[1]
                .split("\n\n<chapter_context>", 1)[0]
            )
            if prompt_context["plan"]["section_id"] != "section_3":
                assert schema["properties"]["row_citations"]["maxItems"] == 0
                assert schema["properties"]["task_payload"]["type"] == "null"
            return fake.fake_draft(arguments["prompt"])
        prompt_context = json.loads(
            arguments["prompt"].split("<review_context>\n", 1)[1].split("\n</review_context>", 1)[0]
        )
        assert prompt_context["claim_evidence_links"] == []
        return json.dumps({"issues": [], "prior_issues": [], "verdict": "approved"})

    output = await write_worker(PhaseInput.from_state(initial), context(initial, invoke))
    written = merge_phase_result(initial, output)
    assert len(prompts) == 5
    assert written.draft_version == 1 and initial.draft_version == 0
    assert all(
        section.content == "\n\n".join(item.text for item in section.statements)
        for section in written.draft_sections.values()
    )
    current = PipelineState.model_validate(written.model_dump() | {"phase": "review"})
    output = await review_worker(PhaseInput.from_state(current), context(current, invoke))
    reviewed = merge_phase_result(current, output)
    assert reviewed.review_verdict == "needs_more_work"  # Gaps cannot become approved.
    assert "受控调试样例" in prompts[-1]  # Review actual text, not bindings only.
    report = build_report(reviewed, report_id=uuid4(), created_at=datetime.now(UTC))
    assert report.markdown and not report.references
    assert report.sections["section_3"].task_payload.task_type == task
    assert report.risks
    await fake.close()


async def test_partial_actual_writer_keeps_untouched_chapter_binding_in_new_version():
    initial = state()
    fake = DebugTools(initial, fake=True)
    calls = []

    async def invoke(tool, arguments):
        calls.append(arguments["prompt"])
        return fake.fake_draft(arguments["prompt"])

    written = merge_phase_result(
        initial, await write_worker(PhaseInput.from_state(initial), context(initial, invoke))
    )
    before = written.draft_claim_bindings
    assert before and all(item.section_id == "section_3" for item in before)
    data = written.model_dump()
    data["run_metadata"]["rework_targets"] = [
        {
            "issue_ids": [],
            "section_ids": ["section_1"],
            "claim_ids": [],
            "action": "revise",
            "reason": "Revise only one chapter",
        }
    ]
    current = PipelineState.model_validate(data)
    tools = context(current, invoke)
    output = await write_worker(PhaseInput.from_state(current), tools)
    newer = merge_phase_result(current, output, target_sections=tools.unit.section_ids)
    assert len(calls) == 6 and newer.draft_version == 2
    assert newer.draft_sections["section_3"].content == written.draft_sections["section_3"].content
    assert newer.draft_claim_bindings == [
        item.model_copy(update={"draft_version": 2}) for item in before
    ]
    assert all(item.draft_version == 1 for item in before)
    await fake.close()


async def test_oversized_writer_context_refuses_before_model_without_silent_clipping():
    from domain.research.agents.writer import draft_chapter

    values = PhaseInput.from_state(state()).values
    values["research_brief"] = values["research_brief"].model_copy(update={"scope": "x" * 200000})

    class Forbidden:
        async def complete(self, prompt):
            raise AssertionError("Oversized context must not reach model")

    with pytest.raises(ValueError, match="exceeds its bound"):
        await draft_chapter(Forbidden(), plan=values["section_plans"][0], values=values, version=1)


def test_factual_output_without_original_binding_is_rejected():
    values = PhaseInput.from_state(state()).values
    output = ChapterDraft(
        title="事实",
        paragraphs=[
            {
                "text": "模型性能已经领先",
                "kind": "factual",
                "claim_ids": [],
                "evidence_ids": [],
                "artifact_ids": [],
            }
        ],
        task_payload=None,
        row_citations=[],
    )
    with pytest.raises(ValueError, match="original evidence"):
        materialize_chapter(output, section_id="section_1", version=1, values=values)


@pytest.mark.parametrize("kind", ["factual", "hypothesis"])
async def test_structured_prompt_keeps_unverified_originals_out_of_factual_prose(kind):
    from domain.research.agents.writer import draft_chapter
    from tests.unit.test_phase_contracts import writing_state
    from tests.unit.test_state import populated_data

    data = writing_state().model_dump()
    fixtures = populated_data()
    for name in ("sources", "evidence", "claims", "claim_evidence_links"):
        data[name] = fixtures[name]
    data["claims"]["c1"].update(spec_ids=["spec-1"], status="insufficient")
    data["claim_evidence_links"][0]["relation"] = "limits"
    data["section_coverage"]["section_1"].update(claim_ids=["c1"], evidence_ids=["e1"])
    initial = PipelineState.model_validate(data)
    values = PhaseInput.from_state(initial).values
    before = initial.model_dump_json()
    prompts = []

    class Model:
        async def complete(self, prompt):
            prompts.append(prompt)
            for section in ("Task", "Materials", "Approach", "Writing Quality", "Examples"):
                assert f"<{section}>" in prompt and f"</{section}>" in prompt
            return json.dumps(
                {
                    "title": "查证边界",
                    "paragraphs": [
                        {
                            "text": "此效果已经证实"
                            if kind == "factual"
                            else "可以检验该效果，当前尚未证实",
                            "kind": kind,
                            "claim_ids": ["c1"],
                            "evidence_ids": ["e1"],
                            "artifact_ids": [],
                        }
                    ],
                    "task_payload": None,
                    "row_citations": [],
                }
            )

    if kind == "factual":
        with pytest.raises(AdapterError, match="violates its schema"):
            await draft_chapter(Model(), plan=initial.section_plans[0], values=values, version=1)
        assert len(prompts) == 2  # A single bounded repair, never a fact upgrade.
    else:
        chapter, bindings = await draft_chapter(
            Model(), plan=initial.section_plans[0], values=values, version=1
        )
        assert len(prompts) == 1 and chapter.statements[0].kind == "hypothesis"
        assert bindings[0].claim_ids == ["c1"] and bindings[0].cited_evidence_ids == ["e1"]
    assert initial.model_dump_json() == before


async def test_core_task_row_factual_without_binding_is_rejected_before_review_model():
    current = insufficient_review(task="method_differentiation")
    data = current.model_dump()
    data["draft_sections"]["section_3"]["statements"][1]["kind"] = "factual"
    values = PhaseInput.from_state(PipelineState.model_validate(data)).values

    class Forbidden:
        async def complete(self, prompt):
            raise AssertionError("An unbound task-row fact must not reach semantic review")

    with pytest.raises(ValueError, match="Factual statement"):
        await review_draft(Forbidden(), values=values)


async def test_review_unknown_target_fails_after_bounded_schema_repair():
    current = insufficient_review()
    values = PhaseInput.from_state(current).values
    calls = []

    class Model:
        async def complete(self, prompt):
            calls.append(prompt)
            return json.dumps(
                {
                    "issues": [
                        {
                            "target_type": "statement",
                            "target_id": "invented",
                            "section_id": "section_1",
                            "issue_type": "overclaim",
                            "severity": "major",
                            "fillable": False,
                            "description": "Unregistered target",
                        }
                    ],
                    "prior_issues": [],
                    "verdict": "needs_more_work",
                }
            )

    with pytest.raises(AdapterError, match="violates its schema"):
        await review_draft(Model(), values=values)
    assert len(calls) == 2


async def test_no_analysis_requirement_records_skip_without_any_model_call():
    initial = state()
    current = PipelineState.model_validate(initial.model_dump() | {"phase": "analyze"})

    async def forbidden(*args):
        raise AssertionError("No requirement needs no model or execution")

    result = await analyze_worker(PhaseInput.from_state(current), context(current, forbidden))
    assert result.changes == {}
    assert result.degradations[0].operation == "analysis_skipped"


@pytest.mark.parametrize("difference", ["unknown", "dataset", "protocol", "unreadable", "same"])
def test_analysis_uses_registered_original_observations_without_fake_artifacts(difference):
    from domain.research.facts import AnalysisRequirement
    from tests.unit.test_scout_extraction import facts, proposal
    from tests.unit.test_scout_originals import original

    _, fetched, parsed, source = original(table=True)
    registered = facts(proposal(parsed), source, fetched, parsed)
    plan = state().section_plans[0]
    requirement = AnalysisRequirement(
        requirement_id="comparison-1",
        operation="comparison_matrix",
        claim_spec_ids=["spec-1"],
        required_context_fields=[],
        parameters={"columns": ["accuracy"]},
    )
    observed = next(iter(registered["quantitative_observations"].values()))
    known = {
        "task": "classification",
        "dataset_and_version": "A-v1",
        "split_or_protocol": "held-out",
        "metric_definition": "correct/total",
    }
    left = observed.model_copy(update={"context": {} if difference == "unknown" else known})
    second = dict(known)
    if difference == "dataset":
        second["dataset_and_version"] = "B-v1"
    if difference == "protocol":
        second["split_or_protocol"] = "temporal"
    right = observed.model_copy(
        update={
            "observation_id": "second-cell",
            "context": {} if difference == "unknown" else second,
            "value": None if difference == "unreadable" else observed.value,
        }
    )
    values = (
        PhaseInput.from_state(state()).values
        | registered
        | {
            "quantitative_observations": {left.observation_id: left, right.observation_id: right},
        }
    )
    changes = assess_requirement(plan, requirement, values)
    group = next(iter(changes["comparison_sets"].values()))
    assert group.comparability == ("compatible" if difference == "same" else "incompatible")
    assert len(group.metric_ids) == 2
    assert next(iter(changes["comparable_metrics"].values())).value == "0"
    assert not changes["analysis_artifacts"]
    assert changes["section_coverage"]["section_1"].gaps


def test_analysis_without_observations_still_judges_requirement_and_records_gap():
    from domain.research.facts import AnalysisRequirement

    values = PhaseInput.from_state(state()).values
    requirement = AnalysisRequirement(
        requirement_id="empty",
        operation="comparison_matrix",
        claim_spec_ids=["spec-1"],
        required_context_fields=[],
        parameters={"columns": ["accuracy"]},
    )
    output = assess_requirement(state().section_plans[0], requirement, values)
    group = next(iter(output["comparison_sets"].values()))
    assert group.comparability == "incompatible" and not group.metric_ids
    assert output["section_coverage"]["section_1"].gaps


def test_prompt_versions_change_with_prompt_not_user_or_secret(monkeypatch):
    from domain.research.agents import prompt_versions, writer

    before = prompt_versions()
    assert before == prompt_versions()
    monkeypatch.setattr(writer, "WRITE_FEW_SHOTS", writer.WRITE_FEW_SHOTS + "新示例")
    after = prompt_versions()
    assert before["write"] != after["write"]
    assert all(before[phase] == after[phase] for phase in before if phase != "write")
