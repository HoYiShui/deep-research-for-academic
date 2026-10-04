# 面向学术研究生命周期的 Deep Research：输入、ResearchBrief 与报告契约

> 状态：终态重构设计草案。本文定义目标业务与接口契约，不代表当前项目原型已经实现 Clarify、多轮恢复、ResearchBrief 或对应 Writer 模板。

## 1. 设计目标

系统面向网络安全等技术研究课题，帮助研究者完成四类高价值工作：研究问题构思、方法差分论证、实验与主张验证，以及后续的审稿意见回应。它的交付不是泛泛的领域综述，而是围绕一次具体研究决策，形成可追溯的证据、比较、结论边界和下一步验证动作。

核心原则：研究请求足够长不代表已经良定义。只有当未确定的信息会改变检索计划、可接受证据、可支持的结论或验证方案时，Architect 才应发起 Clarify。

### 1.1 核心术语：业务语义与架构对象

下表统一文档、交互界面与内部状态的命名。面向研究者时使用左列的业务语义；代码中可使用右列对象名。

| 业务语义 | 架构对象 | 含义 |
|---|---|---|
| 研究目标 | `SectionPlan.objective` | 一个报告章节要支持的研究决策。 |
| 研究论断 | `Claim` / `claims_to_verify` | 需要由证据支持、限制或反驳的核心研究判断。 |
| 检索子问题 | `SectionPlan.sub_questions` | 为核实研究论断而必须回答的事实问题。 |
| 关键检索锚点 | `retrieval_anchors` | 用于定位资料的方法、数据集、协议、指标、标准等条件。 |
| 证据需求 | `evidence_requirements` | 可接受或必须具备的证据类别，如原始论文、数据集文档、官方代码、标准原文。 |
| 检索证据 | `Evidence` | 带来源定位的原文片段、表格、配置或数据说明。 |
| 可回溯证据链 | `Claim` + `Evidence` + `ClaimEvidenceLink` | 记录哪条证据以支持、反驳或限制的关系连接哪项研究论断。 |

“多源佐证”表示来自不同原始论文、数据集文档或官方代码的多条 `Evidence` 共同关联同一 `Claim`。它们不是重复数据；只有同一来源的同一定位片段才应去重。

## 2. 三层契约

```text
用户请求 + 可选研究模式
        ↓
Architect：任务类型识别、Clarify、ResearchBrief 冻结
        ↓
Architect → DeepScout → DataAnalyst/CodeCrafter → Writer → Critic
```

### 2.1 用户入口：研究模式

用户可主动选择，也可交给系统判断：

- 找研究问题 / Idea 构思
- 论证方法差异 / 技术路线选择
- 设计实验与验证主张
- 回应审稿意见或修订论证
- 不确定，由系统判断

这里的“研究模式”是对用户友好的入口。内部实现可将它映射为 `task_type`，无需让用户理解或直接配置 Agent skill。

### 2.2 ResearchBrief：每次研究共享的契约

无论任务类型如何变化，Architect 最终冻结同一粒度的 ResearchBrief：

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

### 2.3 ResearchProfile：按类型展开的专属工作流（已废弃）

> ⚠️ 本节已被 spec 取代：V1 采用统一 10 字段 ResearchBrief + `task_type` 区分任务，不再引入独立的 ResearchProfile 概念。保留仅作历史参考；若将来各 `task_type` 的 brief 结构差异大到需各自加字段，再考虑复活此概念。

ResearchProfile 不重复保存本次研究事实；它定义该类任务需要补充哪些字段、如何组织检索与报告。

| 任务类型 | 决策问题 | 专属字段 | 主要交付 |
|---|---|---|---|
| `idea_exploration` | 哪个问题值得做且能验证 | `research_gap`、`candidate_questions`、`feasibility_constraints` | 候选问题卡与可行性排序 |
| `method_differentiation` | 我的方案能否与最近工作形成可验证差异 | `proposed_method`、`nearest_work`、`differentiation_dimensions` | 方法比较矩阵与差分主张 |
| `evaluation_design` | 如何验证每个主张，以及结论能支持到哪里 | `datasets`、`protocols`、`baselines`、`metrics`、`claim_to_protocol_mapping` | 验证方案与协议—指标—结论映射 |
| `reviewer_response` | 某项质疑是否成立，如何补证或收缩主张 | `reviewer_claim`、`affected_claims`、`available_artifacts` | 回应草案、补证路径与风险判断 |

## 3. Clarify 状态机（政策在 machine.py，Architect 只产判断）

```text
INTAKE
  → 分类 task_type
  → Architect.clarify 读取当前 Brief 草稿，判断缺失项（只产判断，不产 status）
  → machine.decide_status 应用政策：critical 缺口 → ask；否则 → confirm

ask → 返回问题 → 用户回答 → 合并 brief_patch → 再次判断（循环在 session_service）
confirm → 返回完整 Brief → 等待用户确认或要求修改
用户确认 → 冻结 ResearchBrief → 进入 pipeline（PLANNING → DeepScout）
```

每轮 Clarify 建议只问一到两个高信息增益问题。Architect 只输出结构化判断（**不含 status**）：

```yaml
missing_fields: []
questions: []
brief_patch: {}
assumptions: []
```

`status`（ask/confirm）由 `machine.decide_status` 这个纯代码政策决定——**LLM 从不驱动控制流**。`ready` 只在用户明确确认后产生。需要持久化 `session_id`、`clarification_history`、`pending_questions`、`brief_draft` 和 `clarification_round`，以支持用户跨请求回复。设定轮数上限；若仍有非关键缺口，则采用可披露的保守默认值，而不是无限追问。

## 4. 可复用的 Query 骨架

用户的自然语言 Query 可以很具体，但 Architect 需要将其归一到以下要素：

```text
研究动作 + 研究对象 + 决策目标 + 比较范围 + 可用约束 + 证据要求 + 交付形式
```

例如，内部威胁检测只是一个领域实例：

- Idea 构思：围绕某检测任务，识别可在公开数据和既有资源下验证的研究缺口，提出候选问题。
- 方法差分：围绕多源日志检测与事件级溯源，比较不同建模路线，判断拟议方案与最近邻工作可验证的差异。
- 实验设计：针对检测、溯源、泛化等主张，设计数据切分、基线、指标和结果解释边界。

这些实例不应被固化为唯一的安全研究模板；同一套 Brief 契约也应能支持 RAG 安全、漏洞检测、隐私计算或其他技术研究方向。

### 4.1 三类 Query 示例：内部威胁检测

以下示例刻意保持“足以进入 Clarify、但不替用户预先做完研究决策”的粒度。Architect 仍需要根据缺失信息决定是否追问，例如研究对象是否限定某类内部威胁、可用数据与算力、候选路线是否由用户指定、以及交付是否需要实验协议。

#### A. Idea 构思

```text
我希望在公开 CERT 数据上做面向分析员的内部威胁检测研究。请梳理多源日志表征、
事件级溯源与跨版本泛化三个方向的已有证据，识别可在公开数据和有限算力下验证的
研究缺口，并提出 3 个候选研究问题。
```

预期交付：候选研究问题卡，包含问题、已有工作、证据缺口、可验证假设、数据可行性与风险。

#### B. 方法差分 / 技术路线论证

```text
针对“多源日志下的内部威胁检测与事件级溯源”，比较行为特征建模、时序建模、
图建模和弱监督溯源路线，分析它们各自解决的输入、输出和训练问题，并判断我的方案
应如何与最相近工作形成可验证差异。
```

预期交付：方法比较矩阵、最近邻工作、差分主张、适用前提与不可重复的贡献边界。

#### C. 实验与主张验证

```text
我的方法同时输出用户风险分数和事件排序。请针对 CERT r4.2/r6.2、时间切分、
未见用户与跨版本迁移，设计能分别验证检测、溯源和泛化主张的实验协议，并说明
每个协议能支持和不能支持什么结论。
```

预期交付：实验协议、对照组、指标、结果解释边界、风险与补实验建议。

## 5. Writer 的统一 Markdown 报告骨架

```markdown
# {研究标题}

## 0. Research Brief
- 研究类型：{task_type}
- 要解决的决策：{decision_goal}
- 研究对象与范围：{research_object} / {scope}
- 结论边界：{conclusion_boundary}
- 关键假设：{assumptions}

## 1. 问题定义与研究边界
### 1.1 目标问题
### 1.2 系统、数据与威胁/应用边界
### 1.3 本报告不回答的问题

## 2. 证据基础与关键发现
### 2.1 检索范围与信源标准
### 2.2 已确认事实
### 2.3 研究论断与证据缺口

## 3. 核心分析
{task_specific_analysis}

## 4. 可支持的结论与建议
### 4.1 证据支持的结论
### 4.2 适用前提与残余风险
### 4.3 下一步行动建议

## 5. 待验证风险清单
| 风险/未知项 | 当前证据状态 | 影响 | 建议验证方式 |
|---|---|---|---|

## References
```

该骨架保障每份报告都交代研究契约、证据边界和未解决风险；第 3 节再按任务类型替换。

### 5.1 Idea 构思模块

```markdown
## 3. 候选研究问题与可行性评估
### 3.1 已有工作与研究缺口
### 3.2 候选问题卡
| 候选问题 | 可验证假设 | 所需数据/资源 | 新颖性风险 | 可行性 |
|---|---|---|---|---|
### 3.3 推荐问题与最小验证闭环
```

### 5.2 方法差分模块

```markdown
## 3. 技术路线与方法差分论证
### 3.1 候选路线及机制
### 3.2 最近邻工作比较矩阵
| 工作/路线 | 输入与表示 | 核心机制 | 输出 | 解决的限制 | 未解决问题 |
|---|---|---|---|---|---|
### 3.3 可验证差分研究论断
### 3.4 贡献边界与相似性风险
```

### 5.3 实验设计模块

```markdown
## 3. 验证方案设计
### 3.1 研究论断
### 3.2 数据集、切分与基线
### 3.3 协议—指标—结论映射
| 主张 | 实验协议 | 对照组 | 指标 | 能支持的结论 | 不能支持的结论 |
|---|---|---|---|---|---|
### 3.4 失败模式与补实验方案
```

## 6. 与现有原型的关系

现有原型可复用的骨架包括研究状态、章节计划、检索证据归档、章节写作与最终汇总。终态重构需要新增或迁移的内容包括：

- Architect 的任务类型识别与多轮 Clarify；
- 持久化的 ResearchBrief 与研究会话恢复；
- 按 `task_type` 注入的 ResearchProfile、证据标准与章节需求；
- 从通用行业研究报告迁移到技术路线论证报告的 Writer 模板；
- 由章节研究需求决定定性资料、定量数据与图表，而不是按章节位置硬编码。

因此，后续实现应先跑通一个最小闭环：一种任务类型、一次 Clarify、一个冻结 Brief、按 Brief 规划和写出一份对应模板的报告；确认状态恢复、证据回链和章节需求传递后，再逐步扩展其他模式。
