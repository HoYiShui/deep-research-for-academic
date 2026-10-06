"""Pure, deterministic delivery checks and safe report rendering.

These checks prove structure, identity and provenance registration, not semantic
truth. Fetch validates original excerpts; Critic judges the actual same-version
text. Neither a source tier label nor a string match substitutes for those steps.
"""

import re
from datetime import datetime
from hashlib import sha256
from html import escape
from urllib.parse import quote, urlsplit
from uuid import UUID

from domain.research.facts import DraftSection, FinalReport, Reference, RiskItem
from domain.research.ids import canonical_hash, stable_id
from domain.research.machine import decide_pipeline, validate_research_coverage
from domain.research.phase_contracts import SECTION_IDS, PhaseInput
from domain.research.state import PipelineState

TASK_TITLES = {
    "idea_exploration": "候选研究问题与可行性评估",
    "method_differentiation": "技术路线与方法差分论证",
    "evaluation_design": "验证方案设计",
}
KINDS = {"factual": "事实", "hypothesis": "假设", "recommendation": "建议", "limitation": "限制"}
FILE_SUFFIXES = {"png", "jpg", "jpeg", "webp", "pdf", "csv", "json", "txt"}
MAX_REPORT_BYTES = 2 * 1024 * 1024


def literal(value):
    """Untrusted text cannot create Markdown links, headings, tables or raw HTML."""
    text = escape(str(value), quote=True).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"([\\`*_{}\[\]()#!|~>])", r"\\\1", text)
    return text.replace("\n", "<br>")


def safe_url(value):
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or "\\" in value
    ):
        raise ValueError("Report links require a credential-free HTTP(S) URL")
    # Parsing the port also rejects malformed authority strings.
    _ = parsed.port
    return quote(value, safe="/:?#[]@!$&'()*+,;=%")


def statement_anchor(statement_id):
    return "statement-" + sha256(statement_id.encode()).hexdigest()


def draft_content(section: DraftSection):
    """The only accepted free-content projection; no unregistered paragraphs."""
    return "\n\n".join(statement.text for statement in section.statements)


def artifact_files(artifact):
    files = []
    for key in artifact.object_keys:
        parts = key.split("/")
        name = parts[-1]
        if (
            any(not part or part in {".", ".."} for part in parts)
            or "\\" in key
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name)
            or name.rsplit(".", 1)[-1].lower() not in FILE_SUFFIXES
            or name in files
        ):
            raise ValueError("Artifact attachment must have a safe unique whitelisted basename")
        files.append(name)
    if artifact.operation == "plot" and set(artifact.output.get("files", [])) != set(files):
        raise ValueError("Plot file manifest differs from its stored attachments")
    return files


def _payload_rows(payload):
    if payload.task_type == "idea_exploration":
        return payload.candidate_questions, [payload.recommendation, payload.minimal_validation]
    if payload.task_type == "method_differentiation":
        return payload.comparison_rows, [
            *payload.differential_claims,
            payload.contribution_boundary,
        ]
    return payload.protocol_rows, payload.failure_modes


def _texts(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    return []


def _requested_count(token):
    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    if token.lower() in words:
        return words[token.lower()]
    if token.isdecimal():
        return int(token)
    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if token in digits:
        return digits[token]
    if token.count("十") == 1:
        left, right = token.split("十")
        if (not left or left in digits) and (not right or right in digits):
            return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
    raise ValueError("Explicit candidate quantity must be normalized during Clarify")


def _check_payload(state, section):
    payload = section.task_payload
    if payload is None or payload.task_type != state.research_brief.task_type:
        raise ValueError("Section 3 requires the frozen task's structured payload")
    statements = {item.statement_id: item for item in section.statements}
    rows, globals_ = _payload_rows(payload)
    for row in rows:
        if not set(row.statement_ids) <= set(statements) or len(set(row.statement_ids)) != len(
            row.statement_ids
        ):
            raise ValueError("Task row requires known unique section-3 statements")
        registered = "\n".join(statements[key].text for key in row.statement_ids)
        fields = row.model_dump(exclude={"statement_ids", "claim_id"})
        if any(text not in registered for value in fields.values() for text in _texts(value)):
            raise ValueError("Task row contains assertions absent from its registered statements")
        if hasattr(row, "claim_id") and row.claim_id not in state.claims:
            raise ValueError("Evaluation protocol row references an unknown claim")
        if hasattr(row, "claim_id") and state.claims[row.claim_id].text not in registered:
            raise ValueError("Evaluation row's displayed claim must be a registered assertion")
    full_text = draft_content(section)
    if any(text not in full_text for text in globals_):
        raise ValueError("Task payload summary must be registered in section-3 statements")
    if payload.task_type == "idea_exploration":
        # Enforce explicit numeric requests; ambiguous prose remains a Clarify
        # responsibility, not a guessed quantity supplied by the serializer.
        pattern = r"(?P<mode>至少|最少|至多|最多|恰好|at least\s+|at most\s+|exactly\s+)?(?P<count>\d+|[零一二两三四五六七八九十百千万]+|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:个|项)?\s*(?:候选研究问题|候选问题|candidate(?: research)? questions?)"
        for match in re.finditer(pattern, state.research_brief.deliverable, re.IGNORECASE):
            requested, actual = _requested_count(match["count"]), len(rows)
            mode = (match["mode"] or "").strip().lower()
            if (
                (mode in {"至少", "最少", "at least"} and actual < requested)
                or (mode in {"至多", "最多", "at most"} and actual > requested)
                or (
                    mode not in {"至少", "最少", "at least", "至多", "最多", "at most"}
                    and actual != requested
                )
            ):
                raise ValueError("Candidate count does not satisfy the frozen explicit request")


def _check_artifact(state, artifact):
    group = state.comparison_sets[artifact.comparison_set_id]
    requirement = next(
        (
            item
            for plan in state.section_plans
            if plan.section_id == group.section_id
            for item in plan.analysis_requirements
            if item.requirement_id == group.requirement_id
        ),
        None,
    )
    if (
        artifact.execution_status != "completed"
        or group.comparability != "compatible"
        or artifact.template_version
        != state.run_metadata.config.versions.template_versions[artifact.operation]
        or not artifact.input_metric_ids
        or not artifact.input_evidence_ids
        or requirement is None
        or requirement.operation != artifact.operation
    ):
        raise ValueError("Only completed compatible planned pinned-template analysis may be cited")
    original = set()
    required_fields = set(group.required_context_fields) | set(requirement.required_context_fields)
    contexts = []
    for key in artifact.input_metric_ids:
        metric = state.comparable_metrics[key]
        if metric.value is None or metric.missing_context_fields:
            raise ValueError("Computed artifact has incomplete metric inputs")
        context = {field: metric.evaluation_context.get(field) for field in required_fields}
        if any(
            value is None
            or value == ""
            or str(value).lower() in {"unknown", "unspecified", "not reported"}
            for value in context.values()
        ):
            raise ValueError("Unknown comparison conditions cannot become compatible")
        contexts.append(context)
        original.update(
            state.quantitative_observations[obs].evidence_id for obs in metric.observation_ids
        )
    if any(context != contexts[0] for context in contexts[1:]) or original != set(
        artifact.input_evidence_ids
    ):
        raise ValueError("Artifact computation crosses conditions or loses original evidence")
    if artifact.operation != "comparison_matrix":
        metrics = [state.comparable_metrics[key] for key in artifact.input_metric_ids]
        if len({(metric.metric_definition, metric.unit) for metric in metrics}) != 1:
            raise ValueError("Computed artifact mixes metric definitions or units")
    artifact_files(artifact)


def validate_delivery(state: PipelineState):
    state = PipelineState.model_validate_json(state.model_dump_json())
    PhaseInput.from_state(state)
    if state.phase != "review" or not decide_pipeline(state).deliver:
        raise ValueError("Report requires the current reviewed delivery candidate")
    # All planned retrieval requirements remain visible as evidence or gaps.
    validate_research_coverage(state)
    required = {
        item.requirement_id for plan in state.section_plans for item in plan.analysis_requirements
    }
    judged = {group.requirement_id for group in state.comparison_sets.values()}
    if required - judged and state.run_metadata.stop_reason not in {
        "budget_exhausted",
        "deadline_exhausted",
        "rework_limit",
    }:
        raise ValueError("Report silently omits required analysis judgments")
    if set(state.draft_sections) != SECTION_IDS:
        raise ValueError("Report requires five complete draft sections")
    ids = [
        item.statement_id
        for section in state.draft_sections.values()
        for item in section.statements
    ]
    if len(ids) != len(set(ids)):
        raise ValueError("Statement IDs must be unique across the entire report")
    bindings = {(item.section_id, item.statement_id): item for item in state.draft_claim_bindings}
    if len(bindings) != len(state.draft_claim_bindings):
        raise ValueError("Duplicate statement binding")
    specs = {spec.spec_id: spec for plan in state.section_plans for spec in plan.claim_specs}
    for section in state.draft_sections.values():
        if not section.statements or section.content != draft_content(section):
            raise ValueError("Draft content must be exactly the registered statement projection")
        if section.section_id != "section_3" and section.task_payload is not None:
            raise ValueError("Task payload belongs only to section 3")
        for statement in section.statements:
            binding = bindings.get((section.section_id, statement.statement_id))
            if statement.kind == "factual" and (
                binding is None or not binding.claim_ids or not binding.cited_evidence_ids
            ):
                raise ValueError("Every factual statement requires claims and original evidence")
            if binding is None:
                continue  # Nonfactual uncertainty becomes an explicit RiskItem below.
            if any(
                len(set(getattr(binding, field))) != len(getattr(binding, field))
                for field in ("claim_ids", "cited_evidence_ids", "artifact_ids")
            ):
                raise ValueError("Binding identities must be unique")
            for claim_id in binding.claim_ids:
                claim = state.claims[claim_id]
                if not set(claim.spec_ids) <= set(specs):
                    raise ValueError("Cited claim references unplanned specs")
                if statement.kind == "factual":
                    if claim.claim_type not in {
                        "factual",
                        "empirical_comparison",
                    } or claim.status not in {"supported", "limited", "refuted"}:
                        raise ValueError("An open hypothesis cannot support a factual statement")
                    relations = (
                        {"refutes"}
                        if claim.status == "refuted"
                        else {"supports"}
                        if claim.status == "supported"
                        else {"supports", "limits", "refutes"}
                    )
                    linked = [
                        state.evidence[item.evidence_id]
                        for item in state.claim_evidence_links
                        if item.claim_id == claim_id
                        and item.evidence_id in binding.cited_evidence_ids
                        and item.relation in relations
                    ]
                    if not linked:
                        raise ValueError("Cited evidence is not related to its claimed assertion")
                    for spec_id in claim.spec_ids:
                        tiers = specs[spec_id].required_source_tiers
                        if tiers and not any(
                            state.sources[evidence.source_id].source_tier in tiers
                            for evidence in linked
                        ):
                            raise ValueError("Cited evidence does not satisfy planned source tiers")
            related = {
                link.evidence_id
                for link in state.claim_evidence_links
                if link.claim_id in binding.claim_ids
            }
            for artifact_id in binding.artifact_ids:
                artifact = state.analysis_artifacts[artifact_id]
                _check_artifact(state, artifact)
                if not set(artifact.input_evidence_ids) <= set(binding.cited_evidence_ids):
                    raise ValueError("Analysis citation omits its original evidence")
                related.update(artifact.input_evidence_ids)
            if not set(binding.cited_evidence_ids) <= related:
                raise ValueError("Binding includes evidence unrelated to its claims or analysis")
            for evidence_id in binding.cited_evidence_ids:
                evidence = state.evidence[evidence_id]
                source = state.sources[evidence.source_id]
                if (
                    not source.content_object_key
                    or not source.content_hash
                    or not source.provenance
                ):
                    raise ValueError("Citation requires registered original content and provenance")
                if (
                    "snippet" in evidence.extraction_method.lower()
                    or "abstract" in evidence.extraction_method.lower()
                ):
                    raise ValueError("Discovery snippets and abstracts are not original evidence")
                safe_url(source.canonical_url)
                if source.data_classification == "private":
                    frozen = {
                        item.document_version_id for item in state.run_metadata.knowledge_snapshot
                    }
                    if not any(item.document_version_id in frozen for item in source.provenance):
                        raise ValueError("Private citation is outside frozen knowledge scope")
    _check_payload(state, state.draft_sections["section_3"])
    return state


def report_risks(state):
    risks = {}
    for section in state.section_coverage.values():
        for gap in section.gaps:
            key = stable_id("risk", "gap", gap.gap_id)
            risks[key] = RiskItem(
                risk_id=key,
                description=gap.reason,
                evidence_status="证据缺口",
                impact="相关论断尚未闭环",
                verification_action=gap.verification_action,
                claim_ids=[gap.claim_id] if gap.claim_id else [],
                issue_ids=[],
            )
        for text in section.unresolved_items:
            key = stable_id("risk", section.section_id, text)
            risks[key] = RiskItem(
                risk_id=key,
                description=text,
                evidence_status="未解决",
                impact="章节覆盖不完整",
                verification_action="补充对应原文并重新审核",
                claim_ids=[],
                issue_ids=[],
            )
    for issue in state.critic_feedback:
        if not issue.resolved:
            key = stable_id("risk", "issue", issue.issue_id)
            risks[key] = RiskItem(
                risk_id=key,
                description=issue.description,
                evidence_status=f"{issue.severity}: {issue.issue_type}",
                impact="保留审核风险；不升级质量判断",
                verification_action="针对该问题补证、收缩或复核",
                claim_ids=[issue.target_id] if issue.target_type == "claim" else [],
                issue_ids=[issue.issue_id],
            )
    for claim in state.claims.values():
        if claim.status in {"limited", "insufficient", "open"}:
            key = stable_id("risk", "claim", claim.claim_id)
            risks[key] = RiskItem(
                risk_id=key,
                description=claim.status_reason,
                evidence_status=claim.status,
                impact="该论断需保留条件或不得作为确定事实",
                verification_action="核对条件与原始证据，必要时补查",
                claim_ids=[claim.claim_id],
                issue_ids=[],
            )
    judged = {group.requirement_id for group in state.comparison_sets.values()}
    for plan in state.section_plans:
        for requirement in plan.analysis_requirements:
            if requirement.requirement_id not in judged:
                key = stable_id("risk", "analysis", requirement.requirement_id)
                risks[key] = RiskItem(
                    risk_id=key,
                    description="必需分析未完成：" + requirement.requirement_id,
                    evidence_status="最终收缩中未执行",
                    impact="不能形成依赖该计算的结论",
                    verification_action="补充同口径证据并重新执行验证",
                    claim_ids=[],
                    issue_ids=[],
                )
    for group in state.comparison_sets.values():
        if group.comparability != "compatible":
            key = stable_id("risk", "comparability", group.comparison_set_id)
            risks[key] = RiskItem(
                risk_id=key,
                description="；".join(group.reasons),
                evidence_status=group.comparability,
                impact="不能据此数值排序或计算跨条件差值",
                verification_action="核实数据、协议与指标定义",
                claim_ids=[],
                issue_ids=[],
            )
    for artifact in state.analysis_artifacts.values():
        if artifact.execution_status != "completed":
            key = stable_id("risk", "artifact", artifact.artifact_id)
            risks[key] = RiskItem(
                risk_id=key,
                description="分析未成功：" + artifact.artifact_id,
                evidence_status=artifact.execution_status,
                impact="无可引用计算结果",
                verification_action="核查输入、模板与沙箱能力",
                claim_ids=[],
                issue_ids=[],
            )
    bindings = {(item.section_id, item.statement_id): item for item in state.draft_claim_bindings}
    for section in state.draft_sections.values():
        for statement in section.statements:
            binding = bindings.get((section.section_id, statement.statement_id))
            if statement.kind != "factual" and (binding is None or not binding.cited_evidence_ids):
                key = stable_id("risk", "statement", statement.statement_id)
                risks[key] = RiskItem(
                    risk_id=key,
                    description=statement.text,
                    evidence_status=f"未由引用证实的{KINDS[statement.kind]}",
                    impact="不能当作已验证研究结论",
                    verification_action="按冻结任务书验证后再形成事实判断",
                    claim_ids=binding.claim_ids if binding else [],
                    issue_ids=[],
                )
    return [risks[key] for key in sorted(risks)]


def report_references(state):
    cited = {key for binding in state.draft_claim_bindings for key in binding.cited_evidence_ids}
    grouped = {}
    for key in sorted(cited):
        evidence = state.evidence[key]
        grouped.setdefault(evidence.source_id, []).append(evidence)
    references = []
    for index, source_id in enumerate(sorted(grouped), 1):
        source = state.sources[source_id]
        locations = {canonical_hash(item.location): item.location for item in grouped[source_id]}
        references.append(
            Reference(
                reference_id=f"R{index}",
                source_id=source_id,
                title=source.title,
                canonical_url=source.canonical_url,
                version=source.version,
                locations=[locations[key] for key in sorted(locations)],
                evidence_ids=[item.evidence_id for item in grouped[source_id]],
            )
        )
    return references


def location_text(location):
    parts = []
    if location.page_start is not None:
        parts.append(
            f"p.{location.page_start}"
            + (
                f"–{location.page_end}"
                if location.page_end != location.page_start and location.page_end is not None
                else ""
            )
        )
    if location.line_start is not None:
        parts.append(
            f"L{location.line_start}"
            + (f"–{location.line_end}" if location.line_end is not None else "")
        )
    parts.extend(
        str(value)
        for key, value in location.model_dump().items()
        if key not in {"page_start", "page_end", "line_start", "line_end"} and value is not None
    )
    return "/".join(parts)


def _table(headers, rows):
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *[
                "| "
                + " | ".join(
                    literal("；".join(cell) if isinstance(cell, list) else cell) for cell in row
                )
                + " |"
                for row in rows
            ],
        ]
    )


def build_report(state: PipelineState, *, report_id: UUID, created_at: datetime):
    state = validate_delivery(state)
    references, risks = report_references(state), report_risks(state)
    if state.review_verdict == "approved" and risks:
        raise ValueError("Approved cannot hide unresolved gaps or unverified assertions")
    by_evidence = {key: reference for reference in references for key in reference.evidence_ids}
    bindings = {(item.section_id, item.statement_id): item for item in state.draft_claim_bindings}

    def statements(section, kinds=None):
        rendered = []
        for item in section.statements:
            if kinds is not None and item.kind not in kinds:
                continue
            citations = []
            binding = bindings.get((section.section_id, item.statement_id))
            if binding:
                for key in sorted(binding.cited_evidence_ids):
                    reference = by_evidence[key]
                    citations.append(
                        f"[{reference.reference_id}，{literal(location_text(state.evidence[key].location))}](#reference-{reference.reference_id})"
                    )
                for artifact_id in sorted(binding.artifact_ids):
                    for name in artifact_files(state.analysis_artifacts[artifact_id]):
                        path = f"/research/{state.session_id}/artifacts/{quote(artifact_id, safe='')}/files/{quote(name, safe='')}"
                        citations.append(f"[附件：{literal(name)}]({path})")
            rendered.append(
                f'<a id="{statement_anchor(item.statement_id)}"></a>\n\n**{KINDS[item.kind]}**：{literal(item.text)} '
                + " ".join(citations)
            )
        return "\n\n".join(rendered) or "本节无此类已登记论断。"

    brief = state.research_brief
    title = f"{TASK_TITLES[brief.task_type]}：{brief.research_object}"
    lines = [
        f"# {literal(title)}",
        f"审核质量：`{state.review_verdict}`。运行完成不等于研究已获批准。",
        "未闭环说明："
        + ("存在下列已披露风险与待验证事项。" if risks else "当前审核未保留未解决问题。"),
        "## 0. Research Brief",
    ]
    labels = {
        "task_type": "任务类型",
        "decision_goal": "决策目标",
        "research_object": "研究对象",
        "scope": "研究范围",
        "comparison_scope": "比较范围",
        "claims_to_verify": "待验证论断",
        "evidence_requirements": "证据要求",
        "conclusion_boundary": "结论边界",
        "deliverable": "交付要求",
        "assumptions": "关键假设",
    }
    lines.extend(
        f"- {labels[key]}：{literal(value) if value else '无额外假设。'}"
        for key, value in brief.model_dump().items()
    )
    sections = state.draft_sections
    lines.extend(
        [
            "## 1. 问题定义与研究边界",
            "### 1.1 目标问题",
            statements(sections["section_1"]),
            "### 1.2 系统、数据与应用边界",
            literal(brief.scope),
            literal(brief.comparison_scope),
            "### 1.3 不回答的问题",
            literal(brief.conclusion_boundary),
            "## 2. 证据基础与关键发现",
            "### 2.1 检索范围与信源标准",
            literal("、".join(state.source_selection.categories)),
            literal(brief.evidence_requirements),
            "### 2.2 已确认事实",
            statements(sections["section_2"], {"factual"}),
            "### 2.3 论断与证据缺口",
            statements(sections["section_2"], {"hypothesis", "recommendation", "limitation"}),
            f"## 3. {TASK_TITLES[brief.task_type]}",
            statements(sections["section_3"]),
        ]
    )
    payload = sections["section_3"].task_payload
    if payload.task_type == "idea_exploration":
        lines.extend(
            [
                "### 3.1 已有工作与研究缺口",
                "研究缺口仅限本次检索范围，不表示全世界不存在相关工作。",
                "### 3.2 候选问题卡",
                _table(
                    ["问题", "假设", "资源", "新颖性风险", "可行性"],
                    [
                        [
                            row.question,
                            row.hypothesis,
                            row.resources,
                            row.novelty_risk,
                            row.feasibility,
                        ]
                        for row in payload.candidate_questions
                    ],
                ),
                "### 3.3 推荐问题与最小验证",
                literal(payload.recommendation),
                literal(payload.minimal_validation),
            ]
        )
    elif payload.task_type == "method_differentiation":
        lines.extend(
            [
                "### 3.1 机制与最近邻矩阵",
                _table(
                    ["工作", "输入表示", "机制", "输出", "已解决限制", "开放问题"],
                    [
                        [
                            row.work,
                            row.input_representation,
                            row.mechanism,
                            row.output,
                            row.solved_limits,
                            row.open_problems,
                        ]
                        for row in payload.comparison_rows
                    ],
                ),
                "### 3.2 可验证差分论断",
                *[literal(item) for item in payload.differential_claims],
                "### 3.3 贡献边界与相似性风险",
                literal(payload.contribution_boundary),
            ]
        )
    else:
        lines.extend(
            [
                "### 3.1 论断、数据、切分与基线",
                "下列协议是待验证设计，不是已经执行的实验。",
                "### 3.2 协议—指标—结论映射",
                _table(
                    ["论断", "协议", "控制变量", "指标", "可支持结论", "不能支持的结论"],
                    [
                        [
                            state.claims[row.claim_id].text,
                            row.protocol,
                            row.controls,
                            row.metrics,
                            row.supported_conclusions,
                            row.unsupported_conclusions,
                        ]
                        for row in payload.protocol_rows
                    ],
                ),
                "### 3.3 失败模式与补实验",
                *[literal(item) for item in payload.failure_modes],
            ]
        )
    lines.extend(
        [
            "## 4. 可支持的结论与建议",
            "### 4.1 有证据结论",
            statements(sections["section_4"], {"factual"}),
            "### 4.2 适用前提与残余风险",
            statements(sections["section_4"], {"limitation", "hypothesis"}),
            "### 4.3 下一步行动",
            statements(sections["section_4"], {"recommendation"}),
            "## 5. 待验证风险清单",
            statements(sections["section_5"]),
            _table(
                ["风险或未知项", "证据状态", "影响", "验证方式"],
                [
                    [item.description, item.evidence_status, item.impact, item.verification_action]
                    for item in risks
                ],
            )
            if risks
            else "当前审核没有保留待验证风险。",
            "## References",
        ]
    )
    if not references:
        lines.append("无可核验引用。")
    for reference in references:
        url = safe_url(reference.canonical_url)
        lines.append(
            f'<a id="reference-{reference.reference_id}"></a>\n\n[{reference.reference_id}] {literal(reference.title)}；版本：{literal(reference.version or "未标明")}；定位：{literal("；".join(location_text(item) for item in reference.locations))}'
            + (f"；[原始来源](<{url}>)" if url else "；无公开链接。")
        )
    markdown = "\n\n".join(lines) + "\n"
    if len(markdown.encode()) > MAX_REPORT_BYTES:
        raise ValueError("Report exceeds bounded delivery size; cannot silently truncate")
    return FinalReport(
        report_id=report_id,
        session_id=state.session_id,
        run_id=state.run_id,
        version=1,
        brief_version=state.brief_version,
        draft_version=state.draft_version,
        review_verdict=state.review_verdict,
        title=title,
        markdown=markdown,
        sections=state.draft_sections,
        bindings=state.draft_claim_bindings,
        references=references,
        risks=risks,
        created_at=created_at,
    )


def validate_publication(before, report):
    expected = build_report(before, report_id=report.report_id, created_at=report.created_at)
    if expected != report:
        raise ValueError("Published report differs from deterministic reviewed content")
