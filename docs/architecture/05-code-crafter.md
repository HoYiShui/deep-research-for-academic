# 面向学术研究生命周期的 CodeCrafter：受控分析执行与可回溯可视化

> 状态：终态重构设计草案。本文定义 `CodeCrafter` 在网络安全学术研究场景中的分析执行、图表生成与隔离运行边界，不代表当前原型的进程内 `exec()` 已是生产级代码沙箱。

关联文档：

- [DeepScout：递归检索、证据追溯与主张聚合](03-deepscout.md)
- [DataAnalyst：证据口径归一与可比性校核](04-data-analyst.md)
- [ResearchState：状态与数据流契约](02-research-state.md)

## 1. 设计结论

CodeCrafter 的职责是将已获准比较的 `ComparableMetric` 变成可复现的表格、计算结果或图表 `AnalysisArtifact`。它不负责判断数据是否可比，也不应让 LLM 自由生成并直接执行任意 Python。

```text
SectionPlan.analysis_requirements + compatible ComparableMetric
  → 选择受控分析模板
  → 校验输入 Metric / Evidence 的来源
  → 隔离执行计算或绘图
  → AnalysisArtifact
      ├─ 输入指标与证据 ID
      ├─ 操作及模板参数
      ├─ 表格、数值、静态图或 ECharts 配置
      └─ 执行状态与失败原因
```

图表的价值是帮助分析员理解条件一致的路线比较、检查覆盖缺口和沿证据链定位来源；图表本身从不构成新证据。

## 2. 输入、模板与输出

```python
AnalysisArtifact(
    artifact_id,
    section_id,
    input_metric_ids,
    input_evidence_ids,
    operation,             # comparison_matrix / pairwise_delta / plot / statistic
    code_or_recipe,
    output,
    execution_status,
)
```

第一版使用参数化、可测试的模板：

- `comparison_matrix`：同一任务、数据集和协议下的方法比较矩阵；
- `pairwise_delta`：两个方法在明确条件下的数值差；
- `grouped_bar_chart`：同口径多方法比较；
- `line_chart_with_ci`：同一协议下的趋势与不确定性；
- `protocol_coverage_table`：各路线的评测条件与证据覆盖情况。

例如，若 TGCN-DA 与 GRU 均在 CERT r4.2、标签率 60%、相同 ACC 定义下报告十次运行均值，模板可计算 `94.97 - 89.64 = 5.33 pp`。Artifact 同时记录两条 Observation 和原结果表的 Evidence ID。

## 3. 终态执行流

```text
process（仅 ANALYZING）
  → select_requested_analysis
  → select_eligible_metrics（仅 compatible）
  → build AnalysisSpec / 选择模板
  → validate_inputs
  → execute_in_isolated_worker
  → validate_output_provenance
  → write AnalysisArtifact
```

当章节没有分析需求、没有 `compatible` 指标，或输入无法回链时，CodeCrafter 跳过执行并写入原因；它不以空泛的“数据洞察”或默认图表填充报告。

## 4. 可视化设计

### 4.1 研究比较图

静态 PNG/SVG 可作为报告附件；ECharts 配置可作为前端交互层。二者都由同一 Artifact 输入生成，避免出现报告图和页面图使用不同数据的分叉。

ECharts 适合：

- 方法—指标比较矩阵的筛选、悬浮查看协议与来源；
- 各章节证据覆盖和不可直接比较原因；
- `Claim → Evidence → QuantitativeObservation → AnalysisArtifact` 的可回溯链路。

### 4.2 论断—证据链可视化

前端图是 `Claim`、`Evidence`、`ClaimEvidenceLink` 与相关 Artifact 的投影：

```text
研究论断 C1
├─ supports → E1：论文 A，p.7 Table 4
│               └─ QO_001：某协议下的结果单元格
├─ supports → E2：官方复现仓库
└─ limits   → E3：论文 B 的限制说明
```

点击节点应能打开原始来源定位；Critic 和 Writer 读取结构化关系，不依赖图形界面推理。第一版无需 Neo4j，关联表足以提供图数据。

## 5. 代码执行与沙箱边界

“受控”首先来自固定操作与输入 Schema，而非黑名单过滤代码字符串。推荐路径为：`AnalysisSpec` 只允许模板名、合法 Metric ID、聚合维度和已定义参数；执行器将其编译为受审计的实现。

隔离运行环境仍有必要，用于限制 CPU、内存、执行时长、网络和文件系统范围，并保存日志与产物。运行失败时写入 `execution_status=failed` 和结构化错误；瞬态 Worker 错误可在预算内重试。缺少输入或模板不支持并不交给模型编造“修复代码”，而是回流为分析缺口。

当前原型所谓 sandbox 最终在应用进程中 `exec()`，即使有 import 白名单和危险模式过滤，也不能作为安全隔离的实现声明。终态若实现隔离，应使用独立 Worker 或容器，并实际验证资源与权限限制。

## 6. 与当前原型的边界

当前 `CodeCrafter` 在全局 `data_points` 达到阈值后，调用 LLM 生成 Pandas/Matplotlib 代码；失败后最多自我修复三次，并以进程内 `exec()` 运行。按章节生成图表时还会混入全局前若干数据点，因此不能保证条件一致或来源可回链。

终态保留其代码执行与绘图能力，但以可比性校验、受控模板、隔离执行和 `AnalysisArtifact` 回链约束其使用范围。
