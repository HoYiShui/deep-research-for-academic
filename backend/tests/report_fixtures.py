"""Explicit insufficient-evidence reports; never passed off as real research."""

from domain.research.facts import Claim, DraftSection
from domain.research.ids import canonical_hash
from domain.research.reporting import draft_content
from domain.research.state import PipelineState
from tests.unit.test_phase_contracts import writing_state


def proposal_claim():
    return Claim(
        claim_id="c-proposal",
        spec_ids=["spec-3"],
        text="待验证论断：方案在指定条件下是否有效",
        claim_type="hypothesis",
        conditions={},
        status="insufficient",
        status_reason="尚无原始证据，仅用于列出待验证协议",
    )


def insufficient_drafts(version=1, task="evaluation_design"):
    sections = {}
    for index in range(1, 6):
        data = {
            "section_id": f"section_{index}",
            "title": "Controlled insufficient-evidence fixture",
            "content": "placeholder",
            "draft_version": version,
            "statements": [
                {
                    "statement_id": f"statement-{index}",
                    "text": "在本次检索范围内尚无可核验原始证据；不作确定性结论。",
                    "kind": "limitation",
                }
            ],
            "task_payload": None,
        }
        if index == 3:
            if task == "idea_exploration":
                row = {
                    "question": "在限定范围内是否存在可验证研究问题？",
                    "hypothesis": "仅提出待检验假设",
                    "resources": "所需数据尚待确认",
                    "novelty_risk": "未完成已有工作查证",
                    "feasibility": "可行性尚待验证",
                    "statement_ids": ["statement-row"],
                }
                payload = {
                    "task_type": task,
                    "candidate_questions": [row],
                    "recommendation": "先核对原始资料再选择问题",
                    "minimal_validation": "先开展小规模验证",
                }
                globals_ = [payload["recommendation"], payload["minimal_validation"]]
            elif task == "method_differentiation":
                row = {
                    "work": "待查证的最近邻工作",
                    "input_representation": "输入表示待核实",
                    "mechanism": "机制待原文核实",
                    "output": "输出形式待核实",
                    "solved_limits": "已解决限制尚无证据",
                    "open_problems": "开放问题需要补查",
                    "statement_ids": ["statement-row"],
                }
                payload = {
                    "task_type": task,
                    "comparison_rows": [row],
                    "differential_claims": ["差分假设尚待验证"],
                    "contribution_boundary": "不声称已证明新颖性",
                }
                globals_ = [*payload["differential_claims"], payload["contribution_boundary"]]
            else:
                row = {
                    "claim_id": "c-proposal",
                    "protocol": "待制定数据划分与基线协议",
                    "controls": ["控制变量尚待确认"],
                    "metrics": ["指标定义尚待确认"],
                    "supported_conclusions": "当前无可支持的性能结论",
                    "unsupported_conclusions": "不能声称优于其他方法",
                    "statement_ids": ["statement-row"],
                }
                payload = {
                    "task_type": task,
                    "protocol_rows": [row],
                    "failure_modes": ["原始证据不足，需要补查与验证"],
                }
                globals_ = payload["failure_modes"]
            texts = [proposal_claim().text] if task == "evaluation_design" else []
            for key, value in row.items():
                if key not in {"statement_ids", "claim_id"}:
                    texts.extend(value if isinstance(value, list) else [value])
            data["statements"].extend(
                [
                    {
                        "statement_id": "statement-row",
                        "text": "；".join(texts),
                        "kind": "hypothesis",
                    },
                    {
                        "statement_id": "statement-summary",
                        "text": "；".join(globals_),
                        "kind": "recommendation",
                    },
                ]
            )
            data["task_payload"] = payload
        section = DraftSection.model_validate(data)
        sections[section.section_id] = DraftSection.model_validate(
            data | {"content": draft_content(section)}
        )
    return sections


def insufficient_review(state=None, task="evaluation_design"):
    state = state or writing_state()
    data = state.model_dump()
    data["research_brief"]["task_type"] = task
    data["brief_hash"] = canonical_hash(data["research_brief"])
    data.update(
        phase="review",
        section_coverage=writing_state().section_coverage,
        draft_sections=insufficient_drafts(task=task),
        draft_claim_bindings=[],
        claims={"c-proposal": proposal_claim()},
        draft_version=1,
        reviewed_draft_version=1,
        review_verdict="needs_more_work",
    )
    return PipelineState.model_validate(data)
