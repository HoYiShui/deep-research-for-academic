# T024：严格冻结 Brief planner 与真实回归（部分完成）

2026-10-06。依赖 T017 的严格计划验证；为正式阶段执行器先迁移 Worker，未把整个US3提前标完成。

`architect.plan` 接受十字段 ResearchBrief，返回 typed SectionPlan 列表，五章、基本ClaimSpec/检索问题与分析参数经过代码验证。空/缺章/未知字段/重复JSON键不再当成功。与Clarify共用有界structured调用：最多3次总调用、最多1次Schema/truncation修复；timeout/依赖失败明确、CancelledError不重试。SDK真实测试重试0、max_tokens16384；未改用户.env或模型。旧单测/CLI调用改为显式`legacy_plan`，不把遗留宽松行为当正式契约；旧CLI根迁移仍属T021。

真实验证用了配置的 `deepseek-flash`（提供方alias，非不可变版本，T056仍待解决），仅公开的Transformer/CNN NIDS验证方案Brief，不创建Session/Run，不做检索，不产生报告。两次独立调用各1次，无隐含fake。

第一条：52.77s，Schema通过，但人工审查发现业务问题：把协议映射表与失败模式列表误放量化analysis_requirements。原始返回中的例子：

- `req_protocol_conclusion_matrix`：comparison_matrix，columns是protocol_component/control_rule/metric_family/conclusion_type/allowed_conditions/prohibited_conclusion。
- `req_failure_mode_register_count`：aggregation(count)，group_by是failure_mode_id/trigger_condition/handling_action。

这些不是从真实原文QuantitativeObservation推导的数值比较，而应属于Writer任务专属结构。故**第一条不算计划语义验收成功**。完整第一条输出在本轮工具记录，以上是失败摘录；未声称其完成research/analyze。

修正：提示词明确analysis_requirements只用于真实量化观察；不要因为报告需要表格就请求numeric comparison_matrix/aggregation。对仅设计前瞻评测协议、无观察数值分析的任务，协议/指标/结论映射保留在objective/claim_specs，analysis_requirements为空。

第二条：51.73s，1次真实调用，五章均通过严格Schema，全部analysis_requirements为空；原文仍包含数据切分/预处理/训练与调参预算、性能/资源/泛化指标、协议—结论映射、失败模式及Gap，不伪造实测结果。完整JSON：[t024-plan-real.json](t024-plan-real.json)。

- brief_hash：`2244d9630c062b1d9ebd0df8188ac643cb4fcd8b7e0347a45290cef6b70704c8`
- plan_hash：`f998f14c2a2e41d7547b156802dd32d9e1e3ebd1b14237427a775422ccf1216a`

只验证1个evaluation_design输入，不能外推三种任务全覆盖或模型每次都正确；语义约束不能只靠提示词，后续DataAnalyst/交付门仍须以真实Observation/Binding防止假数值。未记录提供方token用量/费用金额，不从调用次数猜费用。

测试：11项planner正反例加入后全量399通过；再加dispatcher7项全量406通过（60.33s）。最后增加公开真实artifact的静态hash/Schema回归，不再次调用模型；该静态测试不标成实时E2E。最终全量结果见后续行。T024仍未勾选，task维度质量门/实际CLI接入与US3真实取证未全部完成。

最终本批全量408 passed，51.93s；随后新增plan→tool callback→typed PhaseResult的dispatcher连接反例，planner/dispatcher目标集21 passed，0.10s（新增这一项未计入前述全量408）。Ruff与diff检查通过。
