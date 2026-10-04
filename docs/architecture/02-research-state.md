# Deep Research 终态 `ResearchState`：状态与数据流契约

> 状态：终态重构设计草案。本文定义迁移到网络安全学术研究场景后的共享状态与阶段数据流，不代表当前项目原型的 `state.py` 已经实现这些对象、校验或路由。

关联文档：

- [输入、ResearchBrief 与报告契约](01-contract.md)
- [DeepScout：递归检索、证据追溯与主张聚合](03-deepscout.md)

## 1. 设计结论

`ResearchState` 是一次研究会话的共享事实底座。它不应是各 Agent 随意追加文本的“杂物箱”，而应保存可定位的来源、证据、研究论断、量化观察和分析产物，使每个阶段都能回答：本结论来自哪里、在什么条件下成立、由谁产生、还缺什么。

状态分为三类：

1. **研究契约**：Architect 冻结的研究范围与章节计划。
2. **研究事实**：DeepScout 追加的来源、证据及其与论断的关系。
3. **派生产物**：DataAnalyst、CodeCrafter、Writer 与 Critic 在研究事实之上生成的可比指标、分析结果、草稿和反馈。

## 2. 顶层状态

```python
ResearchState(
    # Planning 后固定：研究“要解决什么”
    research_brief: ResearchBrief,
    section_plans: list[SectionPlan],

    # Research 追加：研究“找到了什么”
    sources: dict[str, SourceRecord],
    evidence: dict[str, Evidence],
    claims: dict[str, Claim],
    claim_evidence_links: list[ClaimEvidenceLink],
    quantitative_observations: dict[str, QuantitativeObservation],
    section_coverage: dict[str, SectionCoverage],

    # Analyze 追加：研究“哪些结果可以比较、计算或绘图”
    comparable_metrics: dict[str, ComparableMetric],
    analysis_artifacts: dict[str, AnalysisArtifact],

    # Write / Review 追加：研究“如何表达、还缺什么”
    draft_sections: dict[str, DraftSection],
    draft_claim_bindings: list[DraftClaimBinding],
    critic_feedback: list[CriticFeedback],
    final_report: FinalReport | None,

    # 运行控制：不承载研究事实
    phase: ResearchPhase,
    run_metadata: RunMetadata,
)
```

`sources`、`evidence` 等可以实现为 `list` 或数据库表；这里写成 `dict[id, object]` 只是强调稳定 ID 与按 ID 回链。第一版无需图数据库。

## 3. Planning：研究契约

### 3.1 `ResearchBrief`

`ResearchBrief` 是一次研究的**结构化研究任务书**，定义研究范围、比较对象、可接受证据、结论边界和交付形式。Clarify 产生完整 Brief 草稿后，必须经用户确认才冻结；后续若用户改变研究决策，应创建新版本，而不是静默覆盖。

```python
ResearchBrief(
    task_type,             # idea_exploration / method_differentiation / evaluation_design / reviewer_response
    decision_goal,
    research_object,
    scope,
    comparison_scope,
    claims_to_verify,
    evidence_requirements,
    conclusion_boundary,
    deliverable,
    assumptions,
)
```

### 3.2 `SectionPlan`

每个章节计划说明“本章要为哪项研究决策提供什么证据”。其中的研究论断是待核实对象，尚不代表系统已经得出结论。

```python
SectionPlan(
    section_id,
    title,
    objective,             # 本章支持的研究决策
    claim_specs,           # 待证据支持、限制或反驳的研究论断
    sub_questions,         # 为核实论断需回答的事实问题
    retrieval_anchors,     # 方法、数据集、协议、指标、标准等
    evidence_requirements,
    analysis_requirements, # 可选：是否需要比较矩阵、计算或图表
)
```

`analysis_requirements` 只表达研究任务是否需要量化比较或可视化；它不保证存在可比数据，更不授权系统在数据口径不一致时强行绘图。

## 4. Research：来源、证据与研究论断

### 4.1 `SourceRecord`

一份论文、数据集卡、官方代码、标准或本地文档只登记一次。

```python
SourceRecord(
    source_id,             # DOI / arXiv ID / 规范化 URL / repo commit
    source_type,           # paper / dataset / code / standard / local_document
    title,
    authors_or_publisher,
    published_at,
    version,
    canonical_url,
    provenance,           # 获取时间、检索入口、原始链接或本地导入记录
    source_tier,          # primary / official / peer_reviewed / secondary / unknown
)
```

### 4.2 `Evidence`

`Evidence` 是可回到原始资料位置的最小引用单元。它可以是方法段落、实验设置、限制说明、数据集说明，也可以是一整张结果表。

```python
Evidence(
    evidence_id,
    source_id,
    evidence_type,         # method / protocol / result_table / limitation / ...
    location,              # 页码、章节、表格编号、代码行号等
    quote_or_raw_content,
    extraction_method,     # PDF 定位、网页正文抽取、表格解析等
)
```

### 4.3 `Claim` 与 `ClaimEvidenceLink`

Planning 中的 `claim_specs` 在 Research 中被实例化或细化为 `Claim`。研究过程中若发现数据集、协议或指标条件不同，应将条件差异保留在 `Claim.conditions`（`status=limited`），不能把不同条件下的结论强行合并。

```python
Claim(
    claim_id,
    text,
    conditions,            # 任务、数据集、协议、指标等成立条件
    status,                # open / supported / limited / refuted / insufficient
)

ClaimEvidenceLink(
    claim_id,
    evidence_id,
    relation,              # supports / refutes / limits
)
```

`ClaimEvidenceLink` 在业务上是一条关联边，不需要强调有向或无向；稳定的 `claim_id`、`evidence_id` 与 `relation` 已足以表达语义。

### 4.4 `QuantitativeObservation`

它记录论文表格单元格或文本中的原始数值观察。它不是独立来源，而是对某条 `Evidence` 的结构化投影。

```python
QuantitativeObservation(
    observation_id,
    evidence_id,           # 必须回链到原始表格或段落
    kind,                  # benchmark_result / dataset_stat / hyperparameter / resource_cost
    row_key,               # 数据集、标签率、指标等表格行上下文
    column_key,            # 方法、baseline 等列上下文
    value,
    uncertainty,           # 如均值 ± 标准差中的标准差；可为空
    statistic,             # mean / median / rate / count / ...
)
```

示例：一张“方法 × 数据集 × 标签率 × ACC/F1”的实验表是一个 `Evidence(result_table)`，其中每个结果单元格是一个 `QuantitativeObservation`。表格本身通过 `ClaimEvidenceLink` 支持某项研究论断；Observation 只服务于后续口径归一与计算，不再额外承担 Claim 图边。

### 4.5 `SectionCoverage`

这是章节级运行索引，不复制来源、证据或论断实体。

```python
SectionCoverage(
    section_id,
    claim_ids,
    evidence_ids,
    covered_claim_ids,
    gaps,                  # 如缺原始论文、协议、限制证据或可比指标
    unresolved_items,
)
```

## 5. Analyze：可比性判断与可复现分析

### 5.1 `ComparableMetric`

DataAnalyst 读取 `QuantitativeObservation`，把口径一致的观察归一为可比较指标。无法满足比较条件时，应明确标记为不可直接比较。

```python
ComparableMetric(
    comparable_metric_id,
    observation_ids,
    metric_definition,
    evaluated_method,
    evaluation_context={
        "task": ...,
        "dataset_and_version": ...,
        "split_or_protocol": ...,
        "threshold_or_budget": ...,
    },
    value,
    unit,
    comparability,         # compatible / partial / incompatible
    reasons,
)
```

`baseline` 仅在它是当前比较主张的必要条件时进入 `evaluation_context`，不必强制出现在每个原始 Observation 中。

### 5.2 `AnalysisArtifact`

CodeCrafter 仅在 `SectionPlan.analysis_requirements` 明确需要，且相关 `ComparableMetric` 已通过可比性校验时执行确定性计算或制图。

```python
AnalysisArtifact(
    artifact_id,
    section_id,
    input_metric_ids,
    input_evidence_ids,
    operation,             # aggregation / comparison_matrix / plot / statistic
    code_or_recipe,
    output,                # 表格、数值、图像路径或图表配置
    execution_status,
)
```

每个 Artifact 必须记录输入指标和证据 ID；图表是 Artifact 的一种输出，不是独立证据，也不能替代原始论文表格。

## 6. Write 与 Review

### 6.1 `DraftSection`

Writer 根据章节计划、Claim、Evidence、ComparableMetric 和 AnalysisArtifact 形成草稿。草稿中的每项关键结论必须引用 `evidence_id`，并在适用时引用对应的 Artifact；引用关系另以 `DraftClaimBinding` 显式登记，不能只留在自然语言脚注中。

```python
DraftClaimBinding(
    section_id,
    statement_id,          # 段落或关键句的稳定定位
    claim_ids,
    cited_evidence_ids,
    artifact_ids,
)
```

这使系统能够从一条报告结论回到研究论断、原始摘录和分析产物。例如“路线 A 在某跨版本协议下优于路线 B”的句子，应同时绑定其 `Claim`、双方结果表的 `Evidence`、可比性判断和差值计算 Artifact。

### 6.2 `CriticFeedback`

Critic 不是笼统阅读草稿后给出意见，而是沿 `DraftClaimBinding → Claim → ClaimEvidenceLink → Evidence → SourceRecord` 的链路逐项审核。它分两轮执行：首轮审核草稿并提出定向返工，修订后复核原问题是否关闭、是否引入新问题。

首轮检查：

- **来源与证据**：来源是否可定位，是否是原始论文、官方资料或二手转述；摘录是否足以支撑所引用内容。
- **论断与条件**：每项研究论断是否有支持、限制或反驳证据；比较是否越过数据集、协议、指标口径边界。
- **分析与表达**：图表或计算是否能回链到输入 Evidence；草稿是否把“证据不足”写成确定性结论；章节是否覆盖 ResearchBrief 规定的研究目标。

```python
CriticFeedback(
    issue_id,
    target_type,           # source / evidence / claim / artifact / draft_section
    target_id,
    issue_type,            # missing_source / comparability_violation / overclaim / logic_error / hallucination / outdated
    severity,              # critical / major / minor
    fillable,              # bool，仅 missing_source 使用
    description,
    resolved,
)
```

反馈必须定位到具体对象；回流动作由 `machine.route_after_review` 政策表决定（Critic 不产 `required_action`）：

```text
缺原始来源、关键条件或限制证据
  → DeepScout：citation_trace / gap_fill

口径不一致、计算或图表输入不合格
  → DataAnalyst → CodeCrafter

已有证据但表述过强、引用遗漏或章节缺失
  → Writer 修订

无法补齐
  → Writer 写入待验证风险或收紧结论边界
```

修订后的复核只将问题标记为 `resolved`，或记录未解决/新出现的问题；不能因达到轮次上限而将未解决问题伪装为已通过。

## 7. 阶段读写边界

| 阶段 / Agent | 主要读取 | 允许写入 |
|---|---|---|
| Clarify / Architect | 用户请求、历史 Brief 草稿 | `ClarifyAssessment`（missing_fields / questions / brief_patch / assumptions）；不直接冻结 Brief 或写 SectionPlan |
| DeepScout | Brief、SectionPlan、已有 Source/Evidence | `sources`、`evidence`、`claims`、`claim_evidence_links`、`quantitative_observations`、`section_coverage` |
| DataAnalyst | Evidence、Observation、SectionPlan | `comparable_metrics`、`section_coverage` 中的可比性缺口 |
| CodeCrafter | Analysis requirement、ComparableMetric | `analysis_artifacts` |
| Writer | Plan、Claim、Evidence、Metric、Artifact、Coverage | `draft_sections`、`draft_claim_bindings`、`final_report` |
| Critic | DraftBinding、Claim、Evidence、Source、Metric、Artifact、Coverage | `critic_feedback`、`section_coverage` 中的新增缺口与返工路由 |

## 8. 全链路数据流

```text
用户请求
  → Clarify
  → 完整 Brief 草稿 → 用户确认 → ResearchBrief
  → Architect.plan → SectionPlan[]
  → DeepScout
      → SourceRecord
      → Evidence
      → Claim + ClaimEvidenceLink
      → QuantitativeObservation
      → SectionCoverage
  → DataAnalyst
      → ComparableMetric 或“不可直接比较”原因
  → CodeCrafter（仅满足分析前提时）
      → AnalysisArtifact
  → Writer
      → DraftSection + DraftClaimBinding / FinalReport
  → Critic
      → 定向补查 / 重新分析 / 修订
      → 修订后复核问题关闭情况
```

## 9. 与当前原型的边界

当前原型的 `ResearchState` 主要保存 `outline`、`facts`、`data_points`、`charts`、`code_executions` 与草稿；`DataAnalyst` 从全局事实提取市场型数据点，`CodeCrafter` 将全局前若干数据点混入章节图表，尚无来源定位、证据—论断关系、口径归一或 Artifact 回链约束。

因此本文是迁移后的状态契约，不应被表述为当前代码已经具备的实现事实。

## 附录 A：从一张真实实验结果表到状态对象

以下示例来自本地博士论文《面向大数据环境的内部威胁检测关键技术研究》的表 4-4（PDF 第 85 页，印刷页码 74）。该表比较 LR、LOF、LODA、LSTM、GRU 与 TGCN-DA 在 CERT r4.2、CERT r6.2、UMD 三个数据集、15% / 30% / 60% 三种标签率下的 ACC 与 F1；正文说明每个模型在每个数据集上运行 10 次，表中报告均值 ± 标准差。

它说明实验结果的自然载体通常是“方法 × 数据集 × 协议条件 × 指标”的**完整对比表**，不是脱离上下文的孤立数字。

### A.1 来源与整表 Evidence

```python
SourceRecord(
    source_id="liximing_2024_thesis",
    source_type="paper",
    title="面向大数据环境的内部威胁检测关键技术研究",
    version="PhD thesis, 2024",
)

Evidence(
    evidence_id="E_table_4_4",
    source_id="liximing_2024_thesis",
    evidence_type="result_table",
    location="PDF p.85 / printed p.74 / Table 4-4",
    caption="Insider Threat Detection Performance Comparison",
    quote_or_raw_content="数据集、标签率、ACC/F1 与 LR/LOF/LODA/LSTM/GRU/TGCN-DA 的均值±标准差结果表",
)
```

`E_table_4_4` 保留完整表头、行分组、脚注和原始定位。系统不应只抽取“94.97”后丢弃它属于哪个数据集、何种标签率、与哪些基线比较的上下文。

### A.2 表格单元格转换为 `QuantitativeObservation`

表中 CERT r4.2、60% 标签率、ACC 一行的两个单元格可表示为：

```python
QuantitativeObservation(
    observation_id="QO_001",
    evidence_id="E_table_4_4",
    kind="benchmark_result",
    row_key={
        "dataset": "CERT r4.2",
        "label_rate": "60%",
        "metric": "ACC",
        "run_count": 10,
    },
    column_key={"method": "TGCN-DA"},
    value=94.97,
    uncertainty=0.72,
    statistic="mean_std",
)

QuantitativeObservation(
    observation_id="QO_002",
    evidence_id="E_table_4_4",
    kind="benchmark_result",
    row_key={
        "dataset": "CERT r4.2",
        "label_rate": "60%",
        "metric": "ACC",
        "run_count": 10,
    },
    column_key={"method": "GRU"},
    value=89.64,
    uncertainty=0.42,
    statistic="mean_std",
)
```

两个 Observation 共享同一 `evidence_id` 和同一行上下文，只是方法列不同。表中其他方法、F1 行、其他数据集和标签率会生成更多 Observation；它们仍可回到同一张原始表。

### A.3 Claim 与 Evidence 的直接关系

对该表范围内的研究论断，图关系只需连接到整表 Evidence：

```python
Claim(
    claim_id="C_tgcn_da_r42_60_acc",
    text="在 CERT r4.2、60% 标签率、ACC 指标的表内比较中，TGCN-DA 的报告值高于 GRU。",
    conditions={
        "dataset": "CERT r4.2",
        "label_rate": "60%",
        "metric": "ACC",
    },
    status="supported",
)

ClaimEvidenceLink(
    claim_id="C_tgcn_da_r42_60_acc",
    evidence_id="E_table_4_4",
    relation="supports",
)
```

`QuantitativeObservation` 不再额外作为 Claim 的图节点。它是 `E_table_4_4` 的结构化投影，供后续程序计算；整表才是可供 Writer 引用、供分析员复核的原始证据。

### A.4 DataAnalyst 的可比性归一与 CodeCrafter 的分析产物

由于 `QO_001` 与 `QO_002` 的数据集、标签率、指标和统计口径一致，DataAnalyst 可以将它们归入同一比较集合：

```python
ComparableMetric(
    comparable_metric_id="CM_r42_60_acc",
    observation_ids=["QO_001", "QO_002"],
    metric_definition="mean accuracy over 10 runs",
    evaluation_context={
        "dataset_and_version": "CERT r4.2",
        "label_rate": "60%",
        "metric": "ACC",
    },
    comparability="compatible",
    reasons=[],
)
```

若该章节计划要求生成比较矩阵，CodeCrafter 才能基于该 `ComparableMetric` 计算 TGCN-DA 相对 GRU 的 5.33 个百分点差异，并留下完整回链：

```python
AnalysisArtifact(
    artifact_id="A_r42_60_acc_delta",
    section_id="sec_method_comparison",
    input_metric_ids=["CM_r42_60_acc"],
    input_evidence_ids=["E_table_4_4"],
    operation="pairwise_difference",
    code_or_recipe="94.97 - 89.64",
    output={"TGCN-DA_minus_GRU_pp": 5.33},
    execution_status="completed",
)
```

这一段仅演示目标状态契约中的对象关系，并不表示当前项目原型已经能解析这张表、做可比性判断或执行上述计算。
