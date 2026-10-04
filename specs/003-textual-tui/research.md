# Research: pi-tui 端到端验证客户端

## 决策 1：采用 pi-tui，TUI 位于根目录

**决定**：使用 `@earendil-works/pi-tui`，在根目录创建独立 TypeScript `tui/` 包。

**理由**：pi-tui 已提供差分渲染、主屏 scrollback、输入编辑、滚动、Markdown、加载状态和主题。DR4A 不需要为开发验证另建一套终端视觉系统。根目录位置也使 Node 客户端与 Python backend 的 HTTP/SSE 边界一眼可见。

## 决策 2：采用 `TuiMainScreen`，而非全屏替代屏幕

**决定**：v1 使用 `TuiMainScreen`。

**理由**：开发调试需要保留请求、错误、session ID 和 CLI 对照结果；主屏 scrollback 比沉浸式全屏更适合该任务。等核心路径稳定后，再评估 `TuiAltScreen`。

## 决策 3：pi-tui 处理体验，DR4A 处理语义

**决定**：复用 pi-tui 的 Input、Editor、Markdown、Loader、ScrollView、Container、主题与鼠标区域。DR4A 只实现状态条、时间线条目、Brief 确认动作、composer 上下文和 API 客户端。

**理由**：避免“做一个自己的 pi”。自定义组件应表达 Clarify/Brief/SSE 语义，而不是重新设计按钮、颜色或滚动行为。

## 决策 4：匿名开发模式不等于更改正式 API 契约

**决定**：当前 TUI 不调用认证端点、不附带凭据；本地 backend profile 可返回固定开发用户。

**理由**：先验证研究工作流，降低 E2E 变量。正式认证契约仍保留在 `docs/contracts/research-api.md`；认证恢复时作为单独变更处理。

## 决策 5：一个 TypeScript API 客户端是唯一网络入口

**决定**：`src/api-client.ts` 负责 JSON 请求、标准错误、`/health` 和 SSE 帧解析；组件不直接调用 `fetch`。

**理由**：这样可对真实 HTTP/SSE 形状做测试，并确保 UI 没有通过 Python 内部 import 绕过服务端。
