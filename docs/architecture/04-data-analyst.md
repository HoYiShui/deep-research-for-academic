# 面向学术研究生命周期的 DataAnalyst：证据口径归一与可比性校核

> 状态：终态重构设计草案。本文定义 `DataAnalyst` 迁移到网络安全学术研究后的职责与数据契约，不代表当前原型的市场数据抽取、ECharts 生成或知识图谱逻辑已经完成迁移。

关联文档：

- [输入、ResearchBrief 与报告契约](01-contract.md)
- [DeepScout：递归检索、证据追溯与主张聚合](03-deepscout.md)
- [ResearchState：状态与数据流契约](02-research-state.md)

## 1. 设计结论

DataAnalyst 的职责是判断论文中的量化结果能否比较，并将可比较的原始数值归一为 `ComparableMetric`。它不负责从全局文本自由抽取数字，不替 Writer 得出技术路线结论，也不在口径不一致时强行生成排序或图表。

```text
QuantitativeObservation + SectionPlan.analysis_requirements
  → 归一化方法、任务、数据集、协议、指标和单位
  → 分组并校验比较前提
  → ComparableMetric
      ├─ compatible：可直接比较
      ├─ partial：可以有限比较，需声明差异
      └─ incompatible：不可直接比较，记录原因
```

其业务价值是阻止“不同数据集、不同切分协议、不同指标口径的实验数字被画在同一张图里”，使后续的路线比较、计算与可视化具有明确条件。

## 2. 输入与输出

| 对象 | 由谁产生 | DataAnalyst 如何使用 |
|---|---|---|
| `SectionPlan.analysis_requirements` | ChiefArchitect | 判断某章节是否确需比较矩阵、统计计算或图表。 |
| `QuantitativeObservation` | DeepScout | 读取表格单元格或段落数值及其 `evidence_id`。 |
| `Evidence` / `SourceRecord` | DeepScout | 补齐表格位置、数据来源、任务和实验条件。 |
| `Claim` | DeepScout | 确认比较服务于哪项研究论断。 |
| `ComparableMetric` | DataAnalyst | 输出归一后的数值、条件和可比性结论。 |
| `SectionCoverage` | DeepScout / DataAnalyst | 写入“缺少可比指标”或“协议不一致”等缺口。 |

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
        "baseline": ...,
    },
    value,
    unit,
    comparability,         # compatible / partial / incompatible
    reasons,
)
```

`ComparableMetric` 仍能回链到 Observation，再回到原始表格或段落；它不是脱离论文上下文的“排行榜分数”。

## 3. 终态执行流

```text
process（仅 ANALYZING）
  → select_analysis_sections
  → collect_observations
  → normalize_evaluation_context
  → build_comparison_sets
  → check_comparability
  → write ComparableMetric + SectionCoverage
```

### 3.1 选择需要分析的章节

仅处理 `analysis_requirements` 要求量化比较的章节。没有该要求的文献综述章节可以直接交给 Writer；系统不为“展示能力”强制制造图表。

### 3.2 归一化评测上下文

每条 Observation 至少补齐：任务、方法、数据集及版本、数据切分或时间协议、指标定义、阈值/告警预算、基线、数值和单位。字段缺失并不由模型猜测，而是标记为缺口，必要时交给 DeepScout 补查原文或官方配置。

LLM 可以辅助识别术语别名，例如将论文中的 `top-k alert recall` 映射为已定义的指标候选；但它必须返回映射依据。最终比较资格由结构化字段和确定性规则判断。

### 3.3 校验可比性

`compatible` 的最低前提是比较主张要求的任务、数据集版本、协议和指标定义一致。`partial` 表示有明确且可解释的差异，例如相同数据集但标签率不同；`incompatible` 表示差异足以阻断直接优劣判断。

例如，CERT r4.2 的随机切分 F1 与 CERT r6.2 的跨版本 Recall@alert budget 都可能来自内部威胁检测论文，但它们不能构成“方法 A 优于方法 B”的直接证据。

## 4. 与 CodeCrafter、Writer、Critic 的接口

- CodeCrafter 只接收 `compatible` 的 Metric 和章节分析要求，生成矩阵、差值、统计或图表 Artifact。
- Writer 可以使用 `partial` 的 Metric，但必须说明条件差异，不能写成直接比较结论。
- Critic 检查报告是否越过 `comparability` 边界；若发现条件缺失或误比较，反馈应路由到 DataAnalyst 或 DeepScout。

## 5. 与当前原型的边界

当前 `DataAnalyst` 从 `facts[:20]` 让 LLM 抽取市场规模、增长率和份额等 `data_points`，随后同时生成实体图和 ECharts 配置。它没有以来源定位的 Observation 为输入，也没有评测协议归一、可比性校验或 Artifact 回链。

终态保留 Agent 名称，但将职责收束为“证据口径归一与可比性校核”；知识图谱和图表生成不再由它承担。
