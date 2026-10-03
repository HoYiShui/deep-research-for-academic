# 数据模型

> Phase 1 输出。从 spec 的 Key Entities + `docs/architecture/02-research-state.md` 展开。
> 字段级完整 schema 见 `docs/architecture/02-research-state.md`，此处聚焦实体、关系与状态转换。

## 两个 State（clarify 草稿 vs pipeline 真相）

系统有两份状态，冻结是交接点：

| state | 内容 | 归谁 | 存哪 |
|---|---|---|---|
| **SessionState** | brief_draft（草稿）、clarification_history、clarify 状态（ask/ready） | session_service | sessions / briefs 表 |
| **PipelineState** | 冻结 brief（只读输入）+ section_plans / evidence / claims / ... + phase + run_metadata | orchestrator | phase_snapshots |

**brief_draft 不是 pipeline state 的字段**；冻结 brief 是 pipeline 的输入（只读），交接时传入。

## PipelineState 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| research_brief | ResearchBrief（冻结，只读输入） | 冻结的研究契约 |
| section_plans | list[SectionPlan] | 逐章节计划（plan 阶段产出） |
| sources | dict[str, SourceRecord] | 来源登记（同一来源只登记一次） |
| evidence | dict[str, Evidence] | 证据（带来源定位） |
| claims | dict[str, Claim] | 研究论断 |
| claim_evidence_links | list[ClaimEvidenceLink] | 论断-证据关系 |
| quantitative_observations | dict[str, QuantitativeObservation] | 结果表单元格的结构化投影 |
| comparable_metrics | dict[str, ComparableMetric] | 口径归一后的可比指标 |
| analysis_artifacts | dict[str, AnalysisArtifact] | 分析产物 |
| draft_sections | dict[str, DraftSection] | 章节草稿 |
| draft_claim_bindings | list[DraftClaimBinding] | 草稿结论到证据/产物的绑定 |
| critic_feedback | list[CriticFeedback] | 审阅反馈 |
| final_report | FinalReport \| None | 最终报告 |
| section_coverage | dict[str, SectionCoverage] | 章节覆盖索引（缺口/未决项） |
| phase | ResearchPhase | 阶段 |
| run_metadata | RunMetadata | 运行元数据（预算/轮次/版本） |

## 关键实体

### User（用户）

`user_id`、`email`（unique）、`password_hash`。研究会话归属用户。

### ResearchBrief（研究任务书，10 字段）

`task_type`（枚举：idea_exploration / method_differentiation / evaluation_design / reviewer_response）、`decision_goal`、`research_object`、`scope`、`comparison_scope`、`claims_to_verify`、`evidence_requirements`、`conclusion_boundary`、`deliverable`、`assumptions`。Clarify 后冻结；改动需创建新版本而非静默覆盖。

### SectionPlan（章节计划）

`section_id`、`title`、`objective`、`claim_specs`、`sub_questions`、`retrieval_anchors`、`evidence_requirements`、`analysis_requirements`（可选，是否需要量化比较/图表）。

### Claim / Evidence / ClaimEvidenceLink（证据链核心）

- **Claim**: `claim_id`、`text`、`conditions`（任务/数据集/协议/指标）、`status`（open / supported / limited / refuted / insufficient）
- **Evidence**: `evidence_id`、`source_id`、`evidence_type`、`location`（页码/表格/行号）、`quote_or_raw_content`、`extraction_method`
- **ClaimEvidenceLink**: `claim_id`、`evidence_id`、`relation`（supports / refutes / limits）

去重规则：证据按 `source_id + location + quote` 指纹去重；多源佐证（不同来源的独立 Evidence）保留。

### SourceRecord（来源登记）

`source_id`（DOI/arXiv/URL/commit）、`source_type`（paper/dataset/code/standard/local_document）、`title`、`authors_or_publisher`、`published_at`、`version`、`canonical_url`、`provenance`、`source_tier`（primary/official/peer_reviewed/secondary/unknown）。

### QuantitativeObservation（量化观察）

`observation_id`、`evidence_id`（回链表格）、`kind`、`row_key`、`column_key`、`value`、`uncertainty`、`statistic`。是 Evidence 的结构化投影，不额外承担图边。

### ComparableMetric（可比指标）

`comparable_metric_id`、`observation_ids`、`metric_definition`、`evaluated_method`、`evaluation_context`（task/dataset/split/threshold/baseline）、`value`、`unit`、`comparability`（compatible / partial / incompatible）、`reasons`。

### AnalysisArtifact（分析产物）

`artifact_id`、`section_id`、`input_metric_ids`、`input_evidence_ids`、`operation`（闭集：comparison_matrix / pairwise_delta / plot / statistic / aggregation）、`code_or_recipe`、`output`、`execution_status`。

### DraftSection / DraftClaimBinding / CriticFeedback（写与审）

- **DraftClaimBinding**: `section_id`、`statement_id`、`claim_ids`、`cited_evidence_ids`、`artifact_ids`
- **CriticFeedback**: `issue_id`、`target_type`、`target_id`、`issue_type`（missing_source / comparability_violation / overclaim / logic_error / hallucination / outdated）、`severity`（critical / major / minor）、`fillable`（bool，仅 missing_source 用）、`description`、`resolved`。**不产 required_action**——回流由 machine.py 的政策表决定。

## 状态转换

- **Clarify（SessionState）**：`ask` → `ready`（由 `decide_status` 纯代码政策决定）。ready 时冻结 brief，交接给 PipelineState。
- **ResearchPhase（PipelineState）**：`plan` → `research` → `analyze` → `write` → `review` →（回流 research/analyze/write）→ `done`。
- **Claim.status**: `open` → `supported` / `limited` / `refuted` / `insufficient`（由关联 Evidence 的 relation 决定）
- **ComparableMetric.comparability**: `compatible` / `partial` / `incompatible`（不可逆，口径归一后确定）

## 回流政策表（machine.py，total + 兜底）

`severity` 只做门槛（critical/major 回流，minor 只记录不回流）；具体动作由 `issue_type`（英文标识）决定，`fillable` 仅对 `missing_source` 生效。`route_after_review` 对多个 action 取最高优先级（re_research > re_analyze > revise > acknowledge_limit）：

| issue_type | fillable | 动作 |
|---|---|---|
| missing_source | true | re_research（补查） |
| missing_source | false | acknowledge_limit（收紧边界） |
| comparability_violation | — | re_analyze（重算） |
| hallucination | — | retract + re_research（撤回结论 + 补证） |
| overclaim | — | revise（修订） |
| outdated / logic_error | — | re_research（视具体） |
| *（兜底） | — | revise |

## 阶段读写边界

| 阶段 | 主要读取 | 允许写入 |
|---|---|---|
| Clarify（SessionState） | 用户请求、brief_draft、历史 | brief_draft、clarification_history |
| Plan | 冻结 brief | section_plans |
| Research | Brief、SectionPlan、已有 Source/Evidence | sources、evidence、claims、links、observations、coverage |
| Analyze | Evidence、Observation、SectionPlan | comparable_metrics、coverage 缺口 |
| Analyze（计算） | analysis_requirements、ComparableMetric | analysis_artifacts |
| Write | Plan、Claim、Evidence、Metric、Artifact、Coverage | draft_sections、draft_claim_bindings、final_report |
| Review | Binding、Claim、Evidence、Source、Metric、Artifact、Coverage | critic_feedback、coverage 新增缺口 |
