# 数据模型

> Phase 1 输出。从 spec 的 Key Entities + `docs/architecture/02-research-state.md` 展开。
> 字段级完整 schema 见 `docs/architecture/02-research-state.md`，此处聚焦实体、关系与状态转换。

## 顶层状态 ResearchState

| 字段 | 类型 | 说明 |
|---|---|---|
| research_brief | ResearchBrief | 冻结的研究契约 |
| section_plans | list[SectionPlan] | 逐章节计划 |
| sources | dict[str, SourceRecord] | 来源登记（同一来源只登记一次） |
| evidence | dict[str, Evidence] | 证据（带来源定位） |
| claims | dict[str, Claim] | 研究论断 |
| claim_evidence_links | list[ClaimEvidenceLink] | 论断-证据关系 |
| quantitative_observations | dict[str, QuantitativeObservation] | 结果表单元格的结构化投影 |
| comparable_metrics | dict[str, ComparableMetric] | 口径归一后的可比指标 |
| analysis_artifacts | dict[str, AnalysisArtifact] | 分析产物 |
| draft_sections | dict[str, DraftSection] | 章节草稿 |
| draft_claim_bindings | list[DraftClaimBinding] | 草稿结论到证据/产物的绑定 |
| critic_feedback | list[CriticFeedback] | 审阅反馈与返工路由 |
| final_report | FinalReport \| None | 最终报告 |
| section_coverage | dict[str, SectionCoverage] | 章节覆盖索引（缺口/未决项） |
| phase | ResearchPhase | 阶段 |
| run_metadata | RunMetadata | 运行元数据（预算/轮次/版本） |

## 关键实体

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

`source_id`（DOI/arXiv/URL/commit）、`source_type`（paper/dataset/code/standard/local_document）、`title`、`authors_or_publisher`、`version`、`canonical_url`、`provenance`、`source_tier`（primary/official/peer_reviewed/secondary/unknown）。

### QuantitativeObservation（量化观察）

`observation_id`、`evidence_id`（回链表格）、`kind`、`row_key`、`column_key`、`value`、`uncertainty`、`statistic`。是 Evidence 的结构化投影，不额外承担图边。

### ComparableMetric（可比指标）

`comparable_metric_id`、`observation_ids`、`metric_definition`、`evaluated_method`、`evaluation_context`（task/dataset/split/threshold/baseline）、`value`、`unit`、`comparability`（compatible / partial / incompatible）、`reasons`。

### AnalysisArtifact（分析产物）

`artifact_id`、`section_id`、`input_metric_ids`、`input_evidence_ids`、`operation`、`code_or_recipe`、`output`、`execution_status`。

### DraftSection / DraftClaimBinding / CriticFeedback（写与审）

- **DraftClaimBinding**: `section_id`、`statement_id`、`claim_ids`、`cited_evidence_ids`、`artifact_ids`
- **CriticFeedback**: `issue_id`、`target_type`、`target_id`、`issue_type`、`severity`、`description`、`required_action`（re_research / re_analyze / revise / acknowledge_limit）、`resolved`

## 状态转换

- **Claim.status**: `open` → `supported` / `limited` / `refuted` / `insufficient`（由关联 Evidence 的 relation 决定）
- **ResearchPhase**: `clarify` → `planning` → `research` → `analyze` → `write` → `review` →（回流 research/analyze/write）→ `done`
- **ComparableMetric.comparability**: `compatible` / `partial` / `incompatible`（不可逆，口径归一后确定）

## 阶段读写边界

| 阶段 | 主要读取 | 允许写入 |
|---|---|---|
| Clarify | 用户请求、历史 Brief | research_brief、section_plans、初始 claim_specs |
| Research | Brief、SectionPlan、已有 Source/Evidence | sources、evidence、claims、links、observations、coverage |
| Analyze | Evidence、Observation、SectionPlan | comparable_metrics、coverage 缺口 |
| Analyze（计算） | analysis_requirements、ComparableMetric | analysis_artifacts |
| Write | Plan、Claim、Evidence、Metric、Artifact、Coverage | draft_sections、draft_claim_bindings、final_report |
| Review | Binding、Claim、Evidence、Source、Metric、Artifact、Coverage | critic_feedback、coverage 新增缺口 |
