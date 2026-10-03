# Feature Specification: 后端调试 CLI

**Feature Branch**: `002-cli`
**Created**: 2026-10-03
**Status**: Draft

**Input**: 面向 Agent（一等公民）与人类开发者（次要用户）的调试入口。当前调试 deep-research-agent 后端需要：`docker compose up` 起中间件、`uvicorn` 起服务、`curl -N` 看转义 JSON、人工猜错，以及散落的 smoke 脚本。需要一个结构化、确定性、可脚本化的 CLI 统一这些流程。

## User Scenarios & Testing

### User Story 1 - 环境体检（doctor）(Priority: P1)

Agent/开发者在「是环境问题还是代码问题」时，先跑体检，一次性确认 PG / Milvus / MinIO / 模型权重 / env 是否就绪。

**Why this priority**: 环境错是最常见的「假 bug」，先排除环境才能定位代码。

**Independent Test**: 给定一个部分损坏的环境（如 PG 未启动），doctor 以退出码 3 退出，并在 stderr 明确指出哪个依赖不通。

**Acceptance Scenarios**:

1. **Given** 全部依赖就绪，**When** 跑 doctor，**Then** 退出码 0，逐项 PASS。
2. **Given** PG 未启动，**When** 跑 doctor，**Then** 退出码 3，stderr 一行指出「postgres 不可达」。

### User Story 2 - 确定性单 agent 调试（slice）(Priority: P1)

Agent 改了一个 agent（如 critic.py）后，只想跑这一个 phase、喂一份固定 state，隔离「是哪个 agent 坏了」。

**Why this priority**: 这是 Agent 调试循环的核心——「改一处 → 跑一次 → 看结果」必须快、确定、可复现。

**Independent Test**: 给定一份罐头 state.json 和 `--fake --seed N`，slice review 两次输出一致，退出码反映 issue_type 是否正确。

**Acceptance Scenarios**:

1. **Given** 一份 state.json + `--fake --seed 42`，**When** 连续跑两次 `slice review`，**Then** 两次 stdout 完全一致。
2. **Given** critic 实现有 bug（漏判 overclaim），**When** `slice review`，**Then** 退出码 1，stderr 一行错误信息。

### User Story 3 - 全链路闭环（run）(Priority: P1)

Agent/开发者想跑一条完整的 clarify → pipeline → report，验证端到端行为。

**Why this priority**: 单 agent 对了不代表全链路对，run 是端到端验收入口。

**Independent Test**: 给定一个 query，run 产出 final_report；给定 `--brief-file` 跳过 clarify 直接进 pipeline。

**Acceptance Scenarios**:

1. **Given** 一个 query + `--fake`，**When** run，**Then** 退出码 0，产出 final_report。
2. **Given** `--brief-file f.json`，**When** run，**Then** 跳过 clarify 直接从冻结 brief 进 pipeline。
3. **Given** `--answers a.json`，**When** run 的 clarify 需要回答，**Then** 自动用罐头答案，不交互。

### User Story 4 - 状态检查（dump）(Priority: P2)

Agent 想搞清楚「为什么卡在这个 phase」，从 phase_snapshots 读 state 打印出来。

**Why this priority**: 排查 pipeline 卡住/异常时，直接看 state 比猜 SSE 事件快。

**Independent Test**: 给定一个 session_id，dump 打印其最新 phase_snapshots.state（`--json` 时是 JSON）。

**Acceptance Scenarios**:

1. **Given** 一个存在的 session_id，**When** dump，**Then** 打印最新 state，退出码 0。
2. **Given** 一个不存在的 session_id，**When** dump，**Then** 退出码 1，stderr 一行「session not found」。

### User Story 5 - KB 单独操作（ingest / search）(Priority: P2)

Agent 想单独验证 KB 入库/检索，不跑完整 pipeline。

**Why this priority**: 隔离 KB 层的 bug（解析 / embedding / 向量检索）。

**Independent Test**: 给定一个 PDF，ingest 入库；给定 query，search 返回 chunks。

**Acceptance Scenarios**:

1. **Given** 一个 PDF 文件，**When** ingest，**Then** 退出码 0，返回 document_id。
2. **Given** 一个 query，**When** search，**Then** 返回 chunks（`--json` 时是 JSON）。

### Edge Cases

- clarify 需要交互，但 CLI 非交互 → `--brief-file` 跳过 / `--answers` 罐头答案。
- LLM 输出非确定 → `--fake --seed` 保证可复现。
- 结果要给人看也要给机器解析 → 默认人类可读 + `--json` 结构化。
- 环境错 vs 代码错要区分 → 退出码 3（环境）vs 1（研究/代码）。
- `--verbose` 的 LLM 原始交互会污染 stdout → 走 stderr。

## Requirements

### Functional Requirements

- **FR-001**: CLI MUST 提供 `doctor` 环境体检，检查 PG / Milvus / MinIO / 模型权重 / env 是否就绪，任一不通过以退出码 3 退出。
- **FR-002**: CLI MUST 支持 `--json`，使 stdout 输出单个可解析的 JSON 对象（`{status, final_report?, error?, events?}`），而非需要 regex 的 pretty 打印。
- **FR-003**: CLI MUST 用退出码区分结果：0 成功 / 1 研究失败 / 2 用法错误 / 3 环境错误。
- **FR-004**: CLI MUST 把错误与日志写到 stderr（一行一条、带时间戳），stdout 只放结果本体。
- **FR-005**: CLI MUST 支持 `--verbose`，把每次 LLM 调用的 prompt + response 打到 stderr，不污染 stdout。
- **FR-006**: CLI MUST 支持 `--fake --seed N`，用内存 fake 适配器确定性复现——`seed` 驱动可复现的多样性（同 seed 同输出、异 seed 异输出），而非固定输出。
- **FR-007**: CLI MUST 提供 `run` 全链路（clarify → pipeline → report）；query 为可选位置参数、与 `--brief-file` 二选一（至少一个），并支持 `--answers`（罐头答案）以满足非交互。
- **FR-008**: CLI MUST 提供 `slice <phase>`，喂一份 `--input state.json`，只跑单个 phase 的 agent；复用 orchestrator 执行单 phase 的那段与 EventBus 事件消费路径（与 router 相同），不另起一套、不加 generator 接口。
- **FR-009**: CLI MUST 提供 `dump <session_id>`，从 phase_snapshots 读 state 打印（唯一 real-mode 命令，需真实 backend 先跑出过快照）。
- **FR-010**: CLI MUST 提供 `ingest` 与 `search`，单独验证 KB 入库/检索。
- **FR-011**: CLI MUST 默认 `--fake`（全内存、秒级、无依赖、确定性），并提供 `--no-fake` 显式关闭以启用真实依赖。

### Key Entities

- **命令集**: run / slice / dump / doctor / ingest / search，每个是 `python -m cli <cmd>` 的子命令。
- **输出契约**: 退出码枚举（0/1/2/3）、stdout 结果本体、stderr 日志、`--json`/`--verbose`/`--quiet` 三个 flag。
- **确定性容器**: 复用基础设施层的 fake 适配器（FakeLLM/FakeSearch/FakeStateStore/...），由 seed 决定输出。

## Success Criteria

- **SC-001**: Agent 跑「改 critic → slice review --fake --json → 看退出码」的循环能在 1 秒内完成单次迭代（fake 模式）。
- **SC-002**: 同一 `--fake --seed N` 输入两次运行的 stdout 完全一致（确定性）。
- **SC-003**: `--json` 输出的结果是一个可由 `json.loads` 解析的单一对象，不含进度噪音。
- **SC-004**: doctor 能正确区分 4 种退出码对应的场景（成功 / 研究失败 / 用法错 / 环境错）。
- **SC-005**: 所有命令都有 `--json` 与人类可读两种输出。

## Assumptions

- CLI 是 deep-research-agent 后端（001 号 feature 的产物）的调试入口，复用其 application / domain / infrastructure 层，不新增研究能力。
- Agent 是一等公民（优先满足其非交互、确定性、结构化需求）；人类开发者是次要用户，但同一 CLI 可用。
- CLI 默认走 `--fake`（内存 fake），真实依赖（deepseek / arxiv / bocha / postgres / milvus）经 doctor 确认后显式启用。
- 本轮聚焦 6 个命令；`serve`（起服务）与 `events`（订阅 SSE）留待后续。
- 文档与代码注释遵循章程约定（代码注释英文、Conventional Commits）。
