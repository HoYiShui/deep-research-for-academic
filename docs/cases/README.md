# cases/ — 端到端 gold 案例

> 本目录的 3 个 case 是「期望输出的完整报告」，作为后续 Agent 系统调试的 diff 基准。

## 这些案例是怎么产出的（复现记录）

这三个 case **不是手写的**，而是用一个「成熟 Agent harness + 前沿模型 + 一份工作流
prompt」跑出来的。记录如下，便于复现与追溯。

### 环境

| 项 | 值 |
|---|---|
| Harness | Claude Code（CLI/TUI） |
| 模型 | deepseek v4.1 flash（模型选择配置为 v4-pro，官方后台自动路由到 v4.1 flash） |
| 产出日期 | 2026-09-29 |
| 补充参考 | `docs/architecture/*.md`（系统设计文档；工作流以内联 prompt 为准，文档仅作细节参考） |

### 使用的 prompt（原文，逐字保留）

```text
你是网络安全学术研究「Deep Research」的执行者。任务：为 3 个端到端 case 产出「完整期望
输出报告」（gold），作为后续 Agent 系统调试的 diff 基准。执行方式是完整走一遍人类专家的
研究工作流，用真实检索（web search + 读原文），绝不编造。

════════ 工作流（严格执行） ════════

Step 1 界定与澄清：
  识别任务类型；澄清 query 隐含约束（如「面向分析员」= 结果可解释、可行动；
  「公开数据 + 有限算力」= 可行性边界）。冻结 ResearchBrief，10 字段：
  task_type（idea_exploration | method_differentiation | evaluation_design）、
  decision_goal、research_object、scope、comparison_scope、claims_to_verify、
  evidence_requirements、conclusion_boundary、deliverable、assumptions。

Step 2 分方向真实检索：
  用 web search / 学术检索，按检索子问题与锚点检索论文、数据集、代码、标准；对关键论断
  递归追溯原始来源（论文原文、数据集文档、官方代码），不停留在摘要或二手转述。

Step 3 逐篇提取证据：
  每条证据 = 来源定位（论文/页码/表格，或数据集版本/URL）+ 原文片段；建立「论断 Claim」
  与证据关系：supports / refutes / limits。只在同一来源同一定位去重，多源佐证保留。

Step 4 口径归一与可比性（涉及量化比较时）：
  归一评测条件（任务 / 数据集版本 / 切分协议 / 指标定义 / 告警预算 / 基线）；
  判断可比性 compatible / partial / incompatible；口径不一致绝不混用、不强行比较。

Step 5 撰写报告：
  按下方骨架写全文；每个结论绑定真实引用、写明成立条件与适用边界；证据不足处写
  「待验证/证据缺口」，不写成确定结论。

Step 6 审阅返工：
  逐项复核结论是否有证据支撑、引用是否回链、是否越过结论边界；证据缺口→补查、
  口径冲突→重算、表述越界→修订；不能补齐→收紧结论边界并列入风险。

════════ 报告骨架（统一） ════════
# {研究标题}
## 0. Research Brief（研究类型 / 决策 / 对象与范围 / 结论边界 / 关键假设）
## 1. 问题定义与研究边界（目标问题 / 系统数据威胁边界 / 本报告不回答的问题）
## 2. 证据基础与关键发现（检索范围与信源标准 / 已确认事实 / 研究论断与证据缺口）
## 3. 核心分析（按任务类型，见下）
## 4. 可支持的结论与建议（证据支持的结论 / 适用前提与残余风险 / 下一步行动）
## 5. 待验证风险清单（表：风险 | 当前证据状态 | 影响 | 建议验证方式）
## References

第 3 节按任务类型：
- idea_exploration：3.1 已有工作与研究缺口 / 3.2 候选问题卡（候选问题|可验证假设|所需数据/资源|新颖性风险|可行性）/ 3.3 推荐问题与最小验证闭环
- method_differentiation：3.1 候选路线及机制 / 3.2 最近邻工作比较矩阵（工作|输入与表示|核心机制|输出|解决的限制|未解决问题）/ 3.3 可验证差分研究论断 / 3.4 贡献边界与相似性风险
- evaluation_design：3.1 研究论断 / 3.2 数据集、切分与基线 / 3.3 协议-指标-结论映射（主张|实验协议|对照组|指标|能支持|不能支持）/ 3.4 失败模式与补实验方案

════════ 硬性约束 ════════
- 真实性优先、可溯源：禁止编造任何引用、论文、数据集、数字、缺口。
- 所有论断与数字来自真实检索，逐条可回溯到真实来源；确证不了的显式标「待核实」。
- 引用必须可定位（论文+页码/表格，或 URL / 数据集版本）。

════════ 三个 case 的原始 query ════════
Case 1（idea_exploration）：
「我希望在公开 CERT 数据上做面向分析员的内部威胁检测研究。请梳理多源日志表征、事件级
溯源与跨版本泛化三个方向的已有证据，识别可在公开数据和有限算力下验证的研究缺口，并
提出 3 个候选研究问题。」

Case 2（method_differentiation）：
「针对"多源日志下的内部威胁检测与事件级溯源"，比较行为特征建模、时序建模、图建模和
弱监督溯源路线，分析它们各自解决的输入、输出和训练问题，并判断我的方案应如何与最相近
工作形成可验证差异。」

Case 3（evaluation_design）：
「我的方法同时输出用户风险分数和事件排序。请针对 CERT r4.2/r6.2、时间切分、未见用户
与跨版本迁移，设计能分别验证检测、溯源和泛化主张的实验协议，并说明每个协议能支持和
不能支持什么结论。」

════════ 输出 ════════
重写 /Users/hoyishui/Workspace/deep-research-for-Academic/docs/cases/ 下 3 个文件：
  case-1-idea-exploration.md / case-2-method-differentiation.md / case-3-evaluation-design.md
每个文件结构：① 原始 query ② ResearchBrief（10 字段） ③ 完整报告全文。
先只做 case-1，完整报告写好后停下来给我看，确认工作流无误再继续 case-2、case-3。

（补充参考，非必需：/Users/hoyishui/Documents/interviewLFG/S4/io_deep-research-for-academic*.md
是系统设计文档，如需细节可查，但工作流以本 prompt 为准。）
```

## 审查评注

每个 case 末尾的「审查评注」是 Claude 复核后的引用核验结果与待修问题记录，非报告正文。
