# architecture/ — 系统设计源文档

本目录是系统的「终态重构设计草案」——Deep Research 系统目标架构的**权威设计源**。它在
Spec Kit 流程中对应 **plan（架构设计）** 阶段，是 `/speckit-plan` 的输入素材；
`docs/contracts/` 是从这里蒸馏出的精简契约，`specs/` 是 WHAT 层规格。

## 来源与来历

这些文档记录了系统从「**行业 / 股票 DeepResearch 原型**」迁移到「**网络安全学术研究
Deep Research 系统**」的设计过程。因此文中保留的「与当前原型的边界」「删除股票数据」等
描述，是在说明重构前后差异，**不代表当前实现事实**——各文档头部都有「状态：终态重构设计
草案」的声明。

## 文档清单

| 文件 | 内容 |
|---|---|
| 01-contract.md | 输入 / ResearchBrief / 报告契约 |
| 02-research-state.md | ResearchState：状态与数据流契约 |
| 03-deepscout.md | DeepScout：递归检索、证据追溯与主张聚合 |
| 04-data-analyst.md | DataAnalyst：证据口径归一与可比性校核 |
| 05-code-crafter.md | CodeCrafter：受控分析执行与可视化 |
| 06-critic.md | Critic：证据约束审核与定向返工 |
| 07-evaluation.md | 离线评测设计 |

## 与其它层的关系

```text
architecture/（设计源，HOW 的完整版）
    ↓ 蒸馏
contracts/（精简契约）  ←→  spec.md（WHAT 规格）  →  plan.md（待生成）
```
