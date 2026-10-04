# ResearchBrief 契约

> 参考：`docs/architecture/01-contract.md` §2.2 / §4。语义字段为 spec 层契约；架构对象列为实现参考。

## 1. 输入归一化：Query 骨架（7 要素）

用户自然语言 Query 归一到以下要素：

```text
研究动作 + 研究对象 + 决策目标 + 比较范围 + 可用约束 + 证据要求 + 交付形式
```

## 2. ResearchBrief（10 字段，Clarify 收敛并经用户确认后冻结）

```yaml
task_type: idea_exploration | method_differentiation | evaluation_design | reviewer_response
decision_goal: 最终要辅助完成的研究决策
research_object: 研究对象、问题域，以及任务输入/输出
scope: 时间、领域、数据、资源与排除范围
comparison_scope: 要比较的技术路线、工作或方案
claims_to_verify: 需要被证据支持、反驳或保留的研究论断
evidence_requirements: 论文、代码、数据集、标准、实验结果等证据要求
conclusion_boundary: 可以支持和不可外推的结论边界
deliverable: 报告、比较矩阵、验证计划、风险清单等交付形式
assumptions: 未澄清但已显式采用的保守默认假设
```

## 3. 三层递进关系

```text
原始 Query
  → 归一化到 7 要素（意图层）
  → Clarify → 完整 Brief 草稿（等待用户确认）
  → 用户确认 → ResearchBrief（10 字段，冻结）
  → 逐章节 SectionPlan（研究目标 / 论断 / 子问题 / 锚点 / 证据需求）
```

## 4. 业务语义 ↔ 架构对象对照

| 业务语义 | 架构对象 | 含义 |
|---|---|---|
| 研究目标 | `SectionPlan.objective` | 一个报告章节要支持的研究决策 |
| 研究论断 | `Claim` / `claims_to_verify` | 需由证据支持、限制或反驳的核心判断 |
| 检索子问题 | `SectionPlan.sub_questions` | 为核实论断必须回答的事实问题 |
| 关键检索锚点 | `retrieval_anchors` | 定位资料的方法/数据集/协议/指标/标准 |
| 证据需求 | `evidence_requirements` | 可接受或必须具备的证据类别 |
| 检索证据 | `Evidence` | 带来源定位的原文片段/表格/配置/数据说明 |
| 可回溯证据链 | `Claim` + `Evidence` + `ClaimEvidenceLink` | 记录证据以支持/反驳/限制的关系连接论断 |

## 5. Clarify 原则

只有当未确定信息会改变检索计划、可接受证据、可支持的结论或验证方案时，才发起 Clarify
（"请求足够长 ≠ 已经良定义"）。每轮只问 1–2 个高信息增益问题；设轮数上限；非关键缺口采用
可披露的保守默认值，不无限追问。系统认为十字段齐备时，必须先向用户展示完整 Brief 并等待明确
确认；只有确认后才冻结，进入 pipeline。
