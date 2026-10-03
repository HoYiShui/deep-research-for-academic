# 面向学术研究生命周期的 Critic：证据约束审核与定向返工

> 状态：终态重构设计草案。本文定义 `Critic` 在网络安全学术研究中的审核与返工路由职责，不代表当前原型已具备报告—论断—证据的程序级绑定或完整复核闭环。

关联文档：

- [DeepScout：递归检索、证据追溯与主张聚合](03-deepscout.md)
- [DataAnalyst：证据口径归一与可比性校核](04-data-analyst.md)
- [CodeCrafter：受控分析执行与可回溯可视化](05-code-crafter.md)
- [ResearchState：状态与数据流契约](02-research-state.md)

## 1. 设计结论

Critic 是研究质量的证据闸门：检查报告中的研究论断是否在明确条件下由可定位证据支撑，并将问题定向回流到检索、数据校核、分析执行或写作环节。它不以一个主观总分替代证据检查，也不能把达到最大轮次等同于审核通过。

```text
DraftClaimBinding
  → Claim
  → ClaimEvidenceLink
  → Evidence
  → SourceRecord
  → 找到具体问题
  → re_research / re_analyze / revise / acknowledge_limit
  → 修订后复核
```

## 2. 审核对象与可追溯链

Writer 对每项关键报告结论生成 `DraftClaimBinding`：

```python
DraftClaimBinding(
    section_id,
    statement_id,
    claim_ids,
    cited_evidence_ids,
    artifact_ids,
)
```

Critic 因而可以从一条结论回到其研究论断、原始论文段落或表格、量化观察与分析 Artifact。例如“路线 A 在跨版本协议下优于路线 B”的句子，必须能定位到双方结果的原始表、比较条件和差值计算。

## 3. 两轮审核

### 3.1 首轮：草稿审核与问题分类

首轮对每个关键 `DraftClaimBinding` 做三层检查：

| 层次 | 审核问题 |
|---|---|
| 来源与证据 | 来源是否可定位，是否为原始论文、官方资料或二手转述；摘录是否支持被引用内容。 |
| 论断与条件 | 是否存在支持、限制或反驳 Evidence；数据集、版本、协议、指标和适用边界是否完整。 |
| 分析与报告 | 比较是否越过可比性边界；图表/计算能否回链输入；章节是否覆盖研究任务书规定的目标。 |

审核“来源真实性”时，Critic 能够核验来源身份、定位、版本和证据与结论的支撑关系；它不能仅凭 Agent 推理证明论文实验在现实中必然为真。无法独立核验的内容应降级为待验证风险，而不是写成事实判定。

### 3.2 第二轮：修订后复核

修订完成后，Critic 以先前 `issue_id` 为锚点检查问题是否真正关闭，并检查修订是否引入新的无依据结论、失效引用或条件遗漏。只有问题被证据或明确的结论收缩处理后，才标记为 `resolved`。

## 4. `CriticFeedback` 与定向返工

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

返工路由（由 `machine.route_after_review` 政策表决定，Critic 只产判断、不产 `required_action`）：

```text
缺原始来源、关键条件或限制证据
  → DeepScout：citation_trace / gap_fill

协议不一致、计算或图表输入不合格
  → DataAnalyst 校核 → CodeCrafter 重建 Artifact

已有证据但语言过强、引用遗漏或章节缺失
  → Writer 修订

无法补齐或预算耗尽
  → Writer 标记待验证风险，收紧结论边界
```

每次返工应绑定原 `issue_id`、目标章节和相关 Claim/Evidence；DeepScout 补得的新证据必须进入相应 Claim 和章节覆盖记录，不能只停留在新的全局搜索结果中。

## 5. 停止边界

- 已无 critical/major 的未解决问题，或用户接受剩余风险时，完成审核；
- 无新增 Evidence、检索/分析预算耗尽、或工具失败时停止该方向返工，并在报告中保留未解决项；
- 达到轮次上限只意味着停止自动返工，不意味着 `approved`；最终状态须区分 `approved`、`needs_more_work` 与 `approved_with_risks`。

## 6. 与当前原型的边界

当前 `Critic` 将截断后的草稿、前若干 facts 与 data points 交给 LLM 审核，并根据问题类型粗略地在重新搜索和修订间路由。它没有 `DraftClaimBinding`，无法逐项校验报告引用与 Claim–Evidence 图；修订路径还可能让 Critic 读取到旧的 `draft_sections` 而非被修订的报告版本。

终态保留其严苛审核与返工角色，但以稳定 ID、证据定位、问题定向和修订后复核构成可检查的质量闭环。
