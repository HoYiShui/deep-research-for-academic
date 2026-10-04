# Feature Specification: pi-tui 端到端验证客户端

**Target Branch**: `feat/pi-tui`

**Spec Directory**: `003-textual-tui`（保留历史目录名；实现不再使用 Textual）

**Created**: 2026-10-04
**Status**: Planned

**Input**: 为 DR4A 增加一个根目录独立的 pi-tui 客户端，用作开发期端到端验证界面。它只经由后端公开 HTTP/SSE 接口走完“创建会话 → 多轮 Clarify → Brief 确认 → pipeline 进度 → 最终报告”路径；不替代 CLI 的冻结 Brief、单 phase 和状态 dump 调试。

本阶段采用本地匿名开发模式：TUI 不显示、不调用或保存注册、登录、token、cookie。后端认证实现保留；正式认证重新接入是独立工作。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 在单一终端时间线完成 Clarify 与 Brief 确认 (Priority: P1)

开发者启动后端与 TUI，直接输入研究请求。TUI 依次显示服务端问题、用户回答和 Brief 草稿；服务端返回 `confirm` 时，TUI 显示完整 Brief，并等待明确的“确认并开始研究”或“要求修改”动作。

**Independent Test**: 使用公开 HTTP 模拟服务或本地匿名开发后端完成 `ask → ask → confirm → ready`；验证确认前绝不连接 SSE。

**Acceptance Scenarios**:

1. **Given** 后端允许匿名开发请求，**When** 用户提交 query，**Then** TUI 创建会话并显示 `session_id`、Clarify 问题和 Brief 草稿。
2. **Given** 响应为 `ask`，**When** 用户提交回答，**Then** 问题、回答和新草稿按发生顺序追加。
3. **Given** 响应为 `confirm`，**When** TUI 收到完整 Brief，**Then** 显示可鼠标点击和可键盘操作的确认/修改动作，不自动启动 pipeline。
4. **Given** 用户确认 Brief，**When** 确认端点返回 `202 ready` 和 `sse_url`，**Then** TUI 才开始观察 SSE。

### User Story 2 - 观察 pipeline 事件、报告与取消 (Priority: P1)

Clarify 完成后，开发者在同一时间线看到 `phase`、`progress`、`rework`、`error`、`done` 及未知 SSE 事件；完成时自动读取报告，也可手动查询、取消。

**Independent Test**: 使用可控 SSE stream 验证事件顺序、error/断开、done 后报告、报告未就绪和取消响应。

### User Story 3 - 以 Pi 风格终端体验调试会话 (Priority: P2)

开发者在 Pi 风格主屏终端保留 scrollback：顶部状态显示 API URL、匿名开发模式、session、状态和 phase；底部 composer 接受 query、Clarify 回答或修改反馈；命令和所有服务端事实均追加到同一时间线。

**Acceptance Scenarios**:

1. **Given** 已创建会话，**When** 查看顶部状态，**Then** 可看到 API URL、匿名开发模式、session ID、会话状态和 phase（若有）。
2. **Given** 新响应或事件到达，**When** TUI 渲染，**Then** 旧条目不被重排或覆盖，用户可向上滚动查看完整证据。
3. **Given** 用户输入 `/status`、`/report`、`/cancel`、`/session`、`/new` 或 `/connect <api-url>`，**Then** 结果追加到时间线，不打开独立页面。
4. **Given** `/connect` 的健康检查失败，**When** 命令完成，**Then** 保留旧 API URL 和会话上下文，并显示失败事实。

### Edge Cases

- 网络错误、超时、非 2xx、未知响应状态、不完整 JSON、SSE 断开或未知事件：显示诊断事实，不制造假数据或成功状态。
- 后端返回 `401`：显示“目标启用了认证，但当前 TUI 是匿名开发客户端”；不弹出登录 UI 或伪造身份。
- Clarify 返回空问题、空 Brief 或错误状态：标记契约异常，不能自行补齐或声称已冻结。
- TUI 退出或重启：v1 不恢复本地 UI 状态；用户可用已显示的 session ID 与 CLI `dump` 继续定位。

## Requirements *(mandatory)*

- **FR-001**: 系统 MUST 在仓库根目录提供独立 `tui/` Node/TypeScript 包，以 `@earendil-works/pi-tui` 构建，不得放入 `backend/` Python 包。
- **FR-002**: TUI MUST 使用 pi-tui 的 `TuiMainScreen`，保留终端 scrollback；v1 不使用 `TuiAltScreen`。
- **FR-003**: TUI MUST 仅通过 `fetch`/HTTP 和 SSE 调用 `docs/contracts/research-api.md` 中的研究接口；不得 import、启动或调用 backend Python 内部对象。
- **FR-004**: TUI MUST 支持 `DR4A_API_URL > --api-url > http://127.0.0.1:8000` 的目标地址优先级；TUI 本身不监听端口。
- **FR-005**: TUI MUST 不显示、不调用或持久化注册、登录、token、cookie；`401` 必须作为“目标意外启用认证”的诊断显示。
- **FR-006**: TUI MUST 支持创建研究会话和多轮 Clarify，并显示 `ask`、`confirm` 状态及原始关键数据。
- **FR-007**: TUI MUST 在 `confirm` 时展示完整 ResearchBrief，提供鼠标及键盘等价的确认/修改操作，且仅在 `ready + sse_url` 后连接 SSE。
- **FR-008**: TUI MUST 在一个按时间追加的流式时间线中显示 Clarify、Brief、SSE、报告、错误和 slash command 结果；不得用视图切换隐藏历史。
- **FR-009**: TUI MUST 支持状态、报告、取消和 `/status`、`/report`、`/cancel`、`/session`、`/new`、`/connect <api-url>`；`/connect` 先 `GET /health`，成功才切换并清空会话。
- **FR-010**: TUI MUST 复用 pi-tui 的输入、滚动、差分渲染、Markdown、加载和主题能力；项目代码只拥有 DR4A 状态投影、API 客户端和少量语义组件。

## Key Entities

- **TUI 会话上下文**: 当前运行期间对一个后端会话的内存投影，包含 session ID、状态、phase、Brief、SSE 地址和时间线。
- **时间线条目**: `system`、`user`、`clarify`、`brief`、`pipeline`、`report`、`error` 或 `command`；带本地顺序号，不能被后续状态覆盖。
- **API 客户端**: TypeScript 模块，唯一负责 JSON 请求、错误解码、SSE 帧解析与健康检查。

## Success Criteria *(mandatory)*

- **SC-001**: 从 `npm run dev` 启动到创建研究会话，开发者不需要手写 HTTP 请求或 `curl`。
- **SC-002**: 至少两轮 Clarify 的问题、回答、草稿、完整 Brief 和明确确认动作按实际顺序显示。
- **SC-003**: 一次 pipeline 会话的 SSE 事件和报告都在同一时间线；错误和中断不显示为完成。
- **SC-004**: TUI 测试覆盖匿名请求、会话创建、两轮 Clarify、Brief 确认、SSE、报告未就绪、取消和 `/connect` 的失败保留/成功清空语义；测试只穿过 HTTP/SSE 边界。

## Assumptions

- 后端与 Compose 中间件仍由开发者按项目既有方式单独启动；TUI 不管理其生命周期。
- `docs/contracts/research-api.md` 仍是正式认证 API 的目标契约。匿名开发 profile 是当前本地验证豁免，不改变未来正式客户端的认证设计。
- CLI 继续负责冻结 Brief、单 phase、状态 dump 与基础设施体检；TUI 和 CLI 互补而不合并。
