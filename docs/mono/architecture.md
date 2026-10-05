# DR4A 后端架构设计

> 状态：写作骨架。本文尚未形成正式架构结论，也不是当前权威设计来源。
>
> 本版本只定义文档结构和写作来源。行号基于 2026-10-05 的仓库内容。后续撰写正文时，必须区分目标设计、产品需求和当前实现。

## 1. 系统上下文与架构原则

本节说明 DR4A 后端是什么、解决什么问题，以及系统边界在哪里。本节应建立全文使用的架构术语和约束。

主要参考：

- `specs/001-deep-research-agent/plan.md:7-41`：系统目标、主要技术、存储类型、部署环境和 V1 约束。
- `specs/001-deep-research-agent/spec.md:102-129`：系统必须提供的核心能力。
- `specs/001-deep-research-agent/spec.md:160-169`：目标用户、交付形态、数据源和 V1 范围。
- `docs/architecture/README.md:3-12`：目标设计文档与当前实现事实的区别。

### 1.1 文档目的与设计范围

本节应说明本文的权威范围。它还应说明本文不定义 API 字段、实体字段、完整数据流和错误码。

参考来源：

- `docs/architecture/README.md:3-12`：现有架构文档的状态和来源。
- `docs/architecture/README.md:26-32`：架构、契约和规格之间的关系。
- `specs/001-deep-research-agent/plan.md:7-13`：计划中的总体架构定义。
- `specs/001-deep-research-agent/spec.md:160-169`：产品范围和实现方案的边界。

### 1.2 核心架构原则

本节应定义少量稳定原则。原则应包括单一事实源、显式状态机、纯任务 Agent、Port/Adapter 和有界执行。

参考来源：

- `specs/001-deep-research-agent/plan.md:43-64`：四层架构、单一事实源、派生事件、纯任务 Agent 和确定性状态机。
- `specs/001-deep-research-agent/plan.md:66-78`：Clarify 和 Pipeline 的不同交互模式。
- `specs/001-deep-research-agent/research.md:5-15`：上述原则的决策理由和被拒绝方案。
- `specs/001-deep-research-agent/spec.md:106-126`：这些原则必须支持的业务能力。

### 1.3 系统边界与外部参与者

本节应包含一张系统上下文图。图中应区分客户端、DR4A 后端、外部服务和持久化系统。

参考来源：

- `specs/001-deep-research-agent/plan.md:15-41`：后端技术环境、存储和部署边界。
- `docs/contracts/research-api.md:1-48`：Web、TUI、Backend API、Session Service 和 Pipeline 的交互边界。
- `specs/002-cli/plan.md:7-17`：CLI 的用途及其复用范围。
- `specs/002-cli/plan.md:69-78`：CLI 是与 HTTP 平级的后端入口。
- `specs/003-textual-tui/plan.md:5-17`：TUI 是独立 Node 进程，只使用公开 HTTP/SSE。
- `specs/003-textual-tui/plan.md:28-44`：TUI 与 Backend 的目录边界和运行时边界。
- `specs/003-textual-tui/research.md:21-31`：匿名开发模式和单一网络入口的限制。

## 2. 系统结构与依赖关系

本节从运行时和源代码两个视角描述系统结构。本节还应定义模块依赖规则和依赖注入位置。

主要参考：

- `specs/001-deep-research-agent/plan.md:43-64`：四层结构和核心组件规则。
- `specs/001-deep-research-agent/plan.md:198-278`：计划中的后端目录、模块职责和结构结论。
- `specs/002-cli/plan.md:19-35`：CLI 模块结构。
- `specs/002-cli/plan.md:69-78`：CLI 与现有层的依赖关系。

### 2.1 运行时组件

本节应描述部署后实际存在的进程、任务、队列和外部系统。它不应使用源代码目录代替运行时组件。

参考来源：

- `specs/001-deep-research-agent/plan.md:15-41`：FastAPI、PostgreSQL、Milvus、进程内状态和目标平台。
- `specs/001-deep-research-agent/plan.md:107-114`：后台 Pipeline、SSE Event Bus、快照和单进程限制。
- `specs/001-deep-research-agent/research.md:17-39`：进程内状态、Redis 演进、PostgreSQL 快照和 SSE 的选择理由。
- `specs/002-cli/plan.md:7-15`：CLI 的运行方式和依赖模式。
- `specs/003-textual-tui/plan.md:5-17`：TUI 的独立运行时和客户端约束。

### 2.2 后端模块与职责

本节应按 `interface`、`application`、`domain` 和 `infrastructure` 描述模块。它还应列出关键服务、状态机和 Agent 的稳定职责。

参考来源：

- `specs/001-deep-research-agent/plan.md:45-58`：四层职责。
- `specs/001-deep-research-agent/plan.md:198-261`：Router、Service、Orchestrator、Domain、Agent 和 Adapter 的计划位置。
- `specs/001-deep-research-agent/plan.md:276-278`：领域逻辑、控制流、HTTP 翻译和 Adapter 的归属。
- `docs/architecture/01-contract.md:27-35`：Architect 到 Critic 的高层职责链。
- `docs/architecture/03-deepscout.md:5-18`：DeepScout 的职责。
- `docs/architecture/04-data-analyst.md:11-25`：DataAnalyst 的职责和禁止事项。
- `docs/architecture/05-code-crafter.md:11-27`：CodeCrafter 的职责和禁止事项。
- `docs/architecture/06-critic.md:12-25`：Critic 的职责和返工边界。
- `docs/implementation/clarify-current.md:51-58`：当前代码中的 Router、Service、Architect、Machine 和 PostgreSQL Adapter 职责。

### 2.3 依赖方向与组合根

本节应分别定义源代码依赖和运行时调用。它还应说明只有组合根可以同时看到抽象接口和具体 Adapter。

参考来源：

- `specs/001-deep-research-agent/plan.md:45-58`：计划中的层级依赖方向。
- `specs/001-deep-research-agent/plan.md:212-245`：Application、Domain Port 和 Infrastructure Adapter 的位置。
- `specs/001-deep-research-agent/plan.md:213-219`：Application Port、组合根和服务模块。
- `specs/001-deep-research-agent/plan.md:276-278`：结构决策和控制流位置。
- `specs/002-cli/plan.md:69-78`：CLI 入口的依赖方向。

## 3. 控制模型与组件协作

本节说明谁控制流程、谁计算状态转换、谁只执行任务。本节只描述协作规则，不复制完整数据流。

主要参考：

- `specs/001-deep-research-agent/plan.md:60-105`：控制权、Clarify、Pipeline 和两个状态的边界。
- `docs/architecture/dataflow.md:77-176`：Orchestrator、Machine 和 Agent 的细粒度协作证据。
- `specs/001-deep-research-agent/data-model.md:81-112`：状态转换、返工政策和阶段读写边界。

### 3.1 控制组件与执行组件

本节应明确 Router、Application Service、SessionService、Orchestrator、Machine、Agent 和 Adapter 的权限。应禁止 Agent 直接决定阶段跳转。

参考来源：

- `specs/001-deep-research-agent/plan.md:60-64`：单一事实源、纯任务 Agent 和 Machine 政策层。
- `specs/001-deep-research-agent/plan.md:212-236`：Application 控制组件和 Domain Agent 的位置。
- `specs/001-deep-research-agent/research.md:5-15`：显式控制流的决策理由。
- `docs/architecture/dataflow.md:79-88`：Orchestrator 调用 Machine、调用 Agent 和合并结果的规则。
- `docs/architecture/06-critic.md:61-92`：Critic 只产判断，Machine 决定返工路由。

### 3.2 Clarify 的控制权

本节应说明 Architect 产出结构化判断，Machine 决定 `ask` 或 `confirm`，SessionService 管理跨请求循环。用户确认是 Pipeline 的启动条件。

参考来源：

- `specs/001-deep-research-agent/plan.md:66-78`：Clarify 的交互模式和职责分配。
- `specs/001-deep-research-agent/plan.md:80-105`：Session 与 Pipeline 的目标交接方式。
- `docs/architecture/01-contract.md:79-101`：Architect、Machine、SessionService 和用户确认的目标规则。
- `docs/contracts/research-api.md:7-48`：客户端、Session Service 和 Pipeline 的目标交互。
- `docs/implementation/clarify-current.md:9-48`：当前 Clarify 链路。
- `docs/implementation/clarify-current.md:60-69`：当前实现与目标控制模型的差异。

### 3.3 Pipeline 的控制权

本节应说明 Orchestrator 管理 Pipeline 循环，Machine 计算下一阶段，Agent 返回结果，Orchestrator 合并状态并发出事件。

参考来源：

- `specs/001-deep-research-agent/plan.md:60-71`：Pipeline 状态机和阶段序列。
- `specs/001-deep-research-agent/plan.md:107-114`：后台任务、快照和事件流。
- `docs/architecture/dataflow.md:60-75`：Pipeline 启动、执行和交付边界。
- `docs/architecture/dataflow.md:79-88`：Pipeline 主循环和结果合并规则。
- `docs/architecture/dataflow.md:90-166`：各阶段的执行角色和返工路由。
- `docs/architecture/dataflow.md:168-176`：每个阶段边界的共同操作。

### 3.4 状态所有权

本节应只说明状态由谁拥有、由谁修改，以及状态在哪里持久化。字段定义应留给 `data-model.md`。

参考来源：

- `specs/001-deep-research-agent/plan.md:77-78`：Clarify 状态的持久化和上下文压缩。
- `specs/001-deep-research-agent/plan.md:80-105`：SessionState、PipelineState 和冻结 Brief 的交接边界。
- `specs/001-deep-research-agent/data-model.md:6-15`：两个 State 的内容、所有者和存储位置。
- `specs/001-deep-research-agent/data-model.md:102-112`：阶段读写权限。
- `docs/architecture/02-research-state.md:289-323`：目标状态的阶段读写边界和全链路关系。

## 4. 外部能力与运行边界

本节说明后端如何使用外部能力，以及 V1 运行环境对架构的限制。本节不定义 Adapter 的详细配置或故障处理参数。

主要参考：

- `specs/001-deep-research-agent/plan.md:107-144`：持久化、认证、本地知识库和存储归属。
- `specs/001-deep-research-agent/plan.md:221-261`：Domain Port 和 Infrastructure Adapter。
- `specs/001-deep-research-agent/research.md:17-51`：运行时、存储、SSE、LLM 和检索源决策。
- `specs/001-deep-research-agent/research.md:59-100`：RAG 组件和存储分层决策。

### 4.1 Port、Adapter 与外部服务

本节应列出核心 Port、对应 Adapter 和外部系统。它还应说明业务模块只能依赖 Port。

参考来源：

- `specs/001-deep-research-agent/plan.md:221-261`：Port 列表和计划中的 Adapter 实现。
- `specs/001-deep-research-agent/research.md:29-51`：Milvus、SSE、LLM 和三类检索源。
- `docs/architecture/03-deepscout.md:34-94`：论文、网页、本地知识库和文档获取能力。
- `docs/architecture/04-data-analyst.md:88-92`：DataAnalyst 与下游组件的接口边界。
- `docs/architecture/05-code-crafter.md:95-101`：代码执行 Port 的安全边界。

### 4.2 存储职责

本节应描述 PostgreSQL、Milvus、MinIO 和进程内存分别保存什么。它不应列出数据库字段。

参考来源：

- `specs/001-deep-research-agent/plan.md:21-29`：不同生命周期的数据存储位置。
- `specs/001-deep-research-agent/plan.md:107-114`：业务快照、事件和取消标志。
- `specs/001-deep-research-agent/plan.md:139-144`：知识库数据的存储归属。
- `specs/001-deep-research-agent/research.md:23-33`：PostgreSQL JSONB 和 Milvus 的选择理由。
- `specs/001-deep-research-agent/research.md:95-100`：PostgreSQL、Milvus 和 MinIO 的分层关系。
- `specs/001-deep-research-agent/data-model.md:6-15`：SessionState 和 PipelineState 的存储归属。

### 4.3 进程、异步任务与事件边界

本节应说明 HTTP 请求、后台 Pipeline、Event Bus 和 SSE 连接之间的运行边界。它还应说明 SSE 不是持久事实源。

参考来源：

- `specs/001-deep-research-agent/plan.md:107-114`：单进程、后台任务、Event Bus 和恢复方式。
- `specs/001-deep-research-agent/research.md:17-21`：V1 进程内状态和 V2 Redis 方案。
- `specs/001-deep-research-agent/research.md:35-39`：选择 SSE 的原因。
- `docs/contracts/research-api.md:268-289`：SSE 的传输语义和非持久性质。
- `specs/002-cli/plan.md:51-60`：HTTP 和 CLI 复用 Event Bus 与 Orchestrator 的规则。

### 4.4 V1 限制与后续演进

本节应列出会改变架构的 V1 限制。它应把未来方案标记为演进方向，而不是当前能力。

参考来源：

- `specs/001-deep-research-agent/plan.md:33-41`：部署平台、规模和恢复约束。
- `specs/001-deep-research-agent/plan.md:107-114`：单 worker、进程内状态和 Redis 演进。
- `specs/001-deep-research-agent/research.md:11-21`：LangGraph 和 Redis 的 V2 定位。
- `specs/001-deep-research-agent/spec.md:160-168`：V1 产品范围和暂缓能力。
- `specs/003-textual-tui/research.md:21-25`：匿名开发模式不是正式认证方案。

## 5. 架构边界与关联文档

本节说明本文在哪里停止。它还应说明其他 mono 文档接管哪些细节，以及如何记录目标设计与当前实现的差异。

主要参考：

- `docs/architecture/README.md:14-32`：现有设计文档的内容和层次关系。
- `specs/001-deep-research-agent/plan.md:184-196`：Spec Kit 产物的原始分工。
- `docs/implementation/clarify-current.md:3-7`：当前实现快照、目标契约和目标数据流的关系。

### 5.1 本文不定义的内容

本节应明确排除 API 字段、实体字段、完整数据流、错误码、重试参数、并发算法和部署步骤。

参考来源：

- `docs/architecture/README.md:26-32`：Architecture、Contract 和 Spec 的原始层次关系。
- `docs/contracts/research-api.md:1-5`：HTTP/SSE 传输契约的权威范围。
- `docs/architecture/dataflow.md:1-4`：细粒度数据流文档的目的。
- `specs/001-deep-research-agent/data-model.md:1-4`：数据模型文档的范围。
- `specs/001-deep-research-agent/plan.md:146-170`：应由 `operations.md` 接管的失败语义、并发和一致性内容。

### 5.2 其他 mono 文档的职责

本节应给出 `architecture.md`、`dataflow.md`、`data-model.md`、`api-contract.md` 和 `operations.md` 的单一职责表。

参考来源：

- `docs/architecture/README.md:14-32`：现有 Architecture 和 Contract 文档的职责。
- `specs/001-deep-research-agent/plan.md:184-196`：Plan、Research、Data Model、Contract 和 Task 的原始关系。
- `docs/contracts/research-api.md:1-5`：API Contract 的权威范围。
- `docs/architecture/dataflow.md:1-4`：Dataflow 的粒度。
- `specs/001-deep-research-agent/data-model.md:1-4`：Data Model 的粒度。

### 5.3 当前实现与目标架构

本节应定义差异记录规则。目标架构不能被当前缺失实现自动降级。当前实现也不能被目标文档描述为已经完成。

参考来源：

- `docs/architecture/README.md:3-12`：目标设计不等于当前实现事实。
- `docs/architecture/03-deepscout.md:1-3`：Agent 目标职责不代表当前主链路已经实现。
- `docs/architecture/06-critic.md:1-10`：Critic 目标设计的状态声明。
- `docs/implementation/clarify-current.md:3-7`：当前实现快照的用途。
- `docs/implementation/clarify-current.md:51-69`：当前 Clarify 责任边界和已知目标偏差。
- `specs/003-textual-tui/contracts/tui-client.md:1-15`：客户端必须报告契约偏差，不能猜测成功状态。
