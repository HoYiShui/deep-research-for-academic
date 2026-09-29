# ResearchProfile 契约

> 参考：`io_deep-research-for-academic.md` §2.3。

ResearchProfile 不重复保存研究事实；它定义每类任务需补充哪些字段、如何组织检索与报告。

| 任务类型 | 决策问题 | 专属字段 | 主要交付 |
|---|---|---|---|
| idea_exploration | 哪个问题值得做且能验证 | `research_gap`、`candidate_questions`、`feasibility_constraints` | 候选问题卡 + 可行性排序 |
| method_differentiation | 方案能否与最近邻形成可验证差异 | `proposed_method`、`nearest_work`、`differentiation_dimensions` | 方法比较矩阵 + 差分主张 |
| evaluation_design | 如何验证每个主张、结论能支持到哪 | `datasets`、`protocols`、`baselines`、`metrics`、`claim_to_protocol_mapping` | 验证方案 + 协议-指标-结论映射 |
| reviewer_response | 质疑是否成立、如何补证或收缩主张 | `reviewer_claim`、`affected_claims`、`available_artifacts` | 回应草案 + 补证路径 + 风险判断 |

> 注：`reviewer_response` 暂缓——其报告第 3 节模块与评测覆盖待定义。
