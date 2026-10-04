# Feature Specification: Textual 端到端验证客户端

**Feature Branch**: `feat/textual-tui`

**Spec Directory**: `003-textual-tui`

**Created**: 2026-10-04

**Status**: Draft

**Input**: 为 DR4A 增加一个轻量的 Textual TUI，用作开发期端到端验证客户端。它作为独立进程，只经由后端公开的 HTTP 与 SSE 接口走完“认证 → 创建研究会话 → 多轮 Clarify → 冻结 Brief → pipeline 进度 → 最终报告”路径；它不是正式 Web 前端，也不替代 CLI 的冻结 Brief / 单 phase 调试能力。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 在 TUI 中完成 Clarify 并得到冻结 Brief (Priority: P1)

开发者启动已运行的后端与 TUI，以一个已认证账号输入开放研究请求。TUI 显示后端返回的问题；开发者可以连续提交回答，直到后端返回 `confirm`。TUI 清晰展示完整 Brief，等待用户明确确认后进入 `ready`，而不是将该过程藏在日志中。

**Why this priority**: Clarify 是当前最难以通过 CLI 验证的真实交互入口；没有这个闭环，就无法发现会话、鉴权、问题收敛或 Brief 契约的断裂。

**Independent Test**: 对一个可用后端完成登录、创建会话并提交多轮回答；断言 TUI 通过 `POST /research` 与 `POST /research/{session_id}/messages` 驱动 Clarify，在 `confirm` 时显示 API 返回的 Brief，并仅经确认端点进入 `ready`。

**Acceptance Scenarios**:

1. **Given** 后端可达且用户已登录，**When** 输入一个研究请求并提交，**Then** TUI 创建会话、显示 `session_id` 与本轮 Clarify 问题。
2. **Given** 会话仍处于 `clarify`，**When** 用户提交回答，**Then** TUI 按时间顺序保留问题与回答，并更新显示后端返回的 Brief 草稿。
3. **Given** 某轮响应状态为 `confirm`，**When** TUI 收到该响应，**Then** 它显示完整 API 返回的 Brief 并等待用户明确确认或要求修改。
4. **Given** 用户确认 Brief，**When** TUI 调用确认端点并收到 `ready` 与 `sse_url`，**Then** 它保存该会话上下文，并将流程切换到 pipeline 进度视图。
5. **Given** 后端拒绝请求（认证、校验或服务错误），**When** 用户提交操作，**Then** TUI 显示状态码与可读错误，不伪造会话状态或 Brief。

---

### User Story 2 - 观察真实 Pipeline 的 SSE 事件和结果 (Priority: P1)

Clarify 完成后，开发者无需抓取 `curl -N` 输出，即可在 TUI 内看到由后端返回的 SSE 地址所推送的 phase、progress、rework、error 与 done 事件；完成后可打开最终报告。

**Why this priority**: 这使 TUI 成为真正的端到端观察面，而不是一个仅能聊天的 Clarify 演示器。

**Independent Test**: 使用能在测试时间内完成的后端配置完成一次 ready 状态；验证 TUI 从 `sse_url` 建立连接、按收到顺序显示事件，并在 `done` 后请求并显示报告。

**Acceptance Scenarios**:

1. **Given** Brief 确认响应带有 `sse_url`，**When** TUI 进入执行视图，**Then** 它连接该 URL，逐条显示事件类型及其载荷中的关键信息。
2. **Given** SSE 推送 `error` 或连接中断，**When** TUI 收到该情况，**Then** 它保留已收事件、明确显示错误，并允许用户查看会话状态；不会把失败标为完成。
3. **Given** SSE 推送 `done`，**When** TUI 请求会话报告，**Then** 它显示报告内容；报告暂未就绪时明确显示后端响应，而非显示空报告。
4. **Given** 用户在 pipeline 运行期间选择取消，**When** TUI 调用取消操作，**Then** 它显示后端返回的 `cancelling` 状态并继续以事件/状态为准更新界面。

---

### User Story 3 - 为调试保留可复查的会话证据 (Priority: P2)

开发者在 TUI 中能查看当前会话 ID、认证与服务连接状态、Clarify 对话、Brief、最近事件和最终报告，从而可以将问题准确地移交给 CLI 的 `dump` 或单 phase 调试。

**Why this priority**: TUI 的目标是缩短定位链路，而不是再造一个不可观测的前端黑箱。

**Independent Test**: 在任一已创建会话中切换各个视图；确认已收到的数据未因视图切换丢失，且 session ID 可被复制/读取以供 CLI 使用。

**Acceptance Scenarios**:

1. **Given** 已创建会话，**When** 用户打开调试信息，**Then** 能看到 session ID、当前 API URL、后端返回的会话状态与 phase（若有）。
2. **Given** 已完成至少一轮 Clarify 或收到至少一条事件，**When** 用户在对话、Brief、事件和报告视图间切换，**Then** 已收到的数据保持可见且顺序不变。
3. **Given** 后端不可达或登录凭据失效，**When** 用户尝试开始或继续会话，**Then** TUI 明确标示连接/认证失败，并允许重新认证而不暴露密码或 token。

### Edge Cases

- 后端健康检查失败、API URL 无法连接或请求超时：显示连接失败及可操作的重试入口；不得暗中启动、修改或重配后端。
- 登录失败、token 缺失或过期：阻止受保护调用，显示认证错误并回到登录流程；访问 token 不写入项目文件、日志或截图。
- Clarify 返回空问题、空 Brief 或与预期不符的状态：原样展示响应并标为契约异常，不能自行补齐或声称已冻结。
- SSE 在 TUI 连接前已结束或中途断开：保留已接收事件，允许查询 `GET /research/{session_id}` 与报告；不假设 SSE 具备重放能力。
- 报告 API 返回 404 或结构异常：明确标示“报告尚未就绪”或响应异常，不显示空白内容为成功。
- TUI 退出或重新启动：v1 不承诺恢复本地 UI 状态；用户可凭已显示的 session ID 用 TUI 或 CLI `dump` 继续诊断。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 提供一个基于 Textual 的独立 TUI 进程；它 MUST 以配置的后端 API URL 工作，且 MUST NOT 通过 import `application`、`domain`、`infrastructure` 或容器对象绕过 HTTP/SSE 边界。
- **FR-002**: TUI MUST 在启动时显示其目标 API URL 和连接状态，并允许开发者在不修改源代码的情况下指定 API URL。
- **FR-003**: TUI MUST 支持注册和登录，遵守 `docs/contracts/research-api.md` 定义的 `POST /auth/register` 与 `POST /auth/login` 契约；认证状态（包括服务端设置的 cookie 或 API 允许的 token）仅保留在当前运行内存中，MUST NOT 写入仓库、默认配置或日志。
- **FR-004**: TUI MUST 按 API 契约为受保护的研究接口及 SSE 连接携带认证状态，并对认证失败给出可读反馈。
- **FR-005**: TUI MUST 支持提交研究 query 创建会话，并显示 `POST /research` 返回的 `session_id`、`ask` 或 `confirm` 状态及其数据；v1 不得假定该响应含 SSE URL。
- **FR-006**: TUI MUST 支持向 `POST /research/{session_id}/messages` 提交多轮 Clarify 回答，并以时间顺序显示每次请求对应的后端问题、状态和 Brief 数据。
- **FR-007**: TUI MUST 在收到 `confirm` 时展示完整 ResearchBrief，并允许用户明确确认或提交修改反馈；它 MUST 仅在确认端点返回 `ready` 且提供 `sse_url` 时连接 SSE，并使用响应实际给出的 URL。
- **FR-008**: TUI MUST 显示现有 SSE 契约中的 `phase`、`progress`、`rework`、`error`、`done` 事件及未知事件的原始载荷，并保持接收顺序。
- **FR-009**: TUI MUST 支持读取 `GET /research/{session_id}` 的会话状态，以及在 `done` 后读取 `GET /research/{session_id}/report` 的报告；报告或状态失败时 MUST 显示错误事实。
- **FR-010**: TUI MUST 支持调用 `POST /research/{session_id}/cancel`，并将后端返回结果与后续观察到的状态/事件区分显示。
- **FR-011**: TUI MUST 在单次运行内保留当前会话的 Clarify 历史、Brief、事件与报告，提供可切换的清晰视图，并始终可见或可取得 session ID。
- **FR-012**: TUI MUST 把网络错误、HTTP 错误、SSE 断开、未知响应状态和不完整响应作为可诊断结果呈现；MUST NOT 吞掉错误、自动制造假数据或将错误显示为 pipeline 完成。
- **FR-013**: 此功能 MUST NOT 自行解释、绕过或改变 `docs/contracts/research-api.md` 定义的研究编排、Clarify、Brief、报告或 API 语义；发现的后端实现偏差应作为验证结果被暴露和记录，留给独立的后端契约符合性工作修复。
- **FR-014**: TUI MUST 以开发期验证为定位；v1 不要求多用户、持久会话列表、token 跨进程持久化、Web 页面、后端自动启动或生产级重连/事件回放。

### Key Entities

- **TUI 会话上下文**: 单次 TUI 运行期间对一个后端研究会话的观察记录，包含 session ID、当前状态/phase、Clarify 对话、Brief、事件和报告；它不是后端持久化模型的替代品。
- **认证上下文**: 当前运行中用于调用受保护接口的 access token 及其连接状态；只驻留内存。
- **Clarify 轮次**: 一次用户回答及后端 `ClarifyResponse` 的配对记录，包含状态、问题、Brief 和（仅 ready 时的）SSE URL。
- **Pipeline 事件记录**: 从单个 SSE 连接实际接收的有序事件及连接错误；不将其误作服务器完整历史。
- **验证证据**: 为定位而呈现的 API URL、session ID、HTTP/SSE 响应与最终报告；可与 CLI `dump <session-id>` 联动，但 TUI 不读取数据库快照。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 在可用的本地后端上，开发者能够从启动 TUI 到创建一个已认证的研究会话，且不需要手写 HTTP 请求或 `curl`。
- **SC-002**: 对一个至少需要两轮 Clarify 的测试请求，TUI 能完整显示每轮问题、回答、状态及 API 返回的 Brief，顺序与实际 HTTP 交互一致。
- **SC-003**: 对一个进入 pipeline 的会话，TUI 显示从 `sse_url` 实际收到的全部事件；若连接或后端返回错误，界面在该操作后立即明确可见失败原因，而不会显示假成功。
- **SC-004**: 开发者可在不查日志或数据库的情况下从任一 TUI 视图取得 session ID，并能据此运行现有 CLI `dump <session-id>`。
- **SC-005**: TUI 的端到端测试覆盖至少：认证失败、会话创建、至少两轮 Clarify、ready 后 SSE、SSE error/断开、报告未就绪和取消请求；测试只经由公开 HTTP/SSE 接口，不直接调用后端内部对象。

## Assumptions

- 后端由开发者按项目既有方式单独启动；TUI 只连接它，不管理 backend、frontend 或 Compose 中间件生命周期。
- TUI 的默认目标是本机开发后端；具体默认 URL 在 plan 阶段确定，但不得成为唯一可用地址。
- 客户端 API 的权威来源是 `docs/contracts/research-api.md`；同一目录同时定义 ResearchBrief 与报告等数据级契约。特别地，Clarify 通过 HTTP 轮次进行，SSE 仅在后端返回 `ready` 和 `sse_url` 后用于 pipeline 事件。
- 该 feature 选择 Textual，是为了与 Python/asyncio 后端保持单运行时的快速验证路径；这是一项实现约束，不将 TUI 定义为正式产品交付形态。
- TUI 是接口级 E2E 验证工具：它可揭示当前会话归属、Brief 完整性、SSE 时序和报告输出问题，但不在本 feature 内修复这些后端业务问题。
- 命令行 CLI 仍负责冻结 Brief 的无交互 pipeline 调试、单 phase 调试、状态 dump 与基础设施体检；两者互补而不合并。
