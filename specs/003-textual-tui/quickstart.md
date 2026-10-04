# pi-tui 快速验证

## 前置条件

在另一个终端按项目既有方式启动后端与所需基础设施。TUI 不会启动 Docker、backend 或 Web 服务。当前 TUI 需要后端启用本地匿名开发 profile；正式认证是后续工作。

需要 Node.js >=22.19。安装依赖和启动命令将在实现时写入 `tui/package.json`，目标形式为：

```bash
cd tui
npm install
npm run dev
```

默认目标为 `http://127.0.0.1:8000`。可通过 `--api-url` 指定临时目标，或以优先级更高的 `DR4A_API_URL` 覆盖；TUI 本身不监听端口。

## 一次完整验证

1. 在 composer 输入研究 query。
2. `ask` 时输入 Clarify 回答；`confirm` 时检查 Brief，使用明确的确认或修改动作。
3. 收到 `ready` 后观察同一时间线中的 SSE 事件与最终报告。
4. 用 `/status`、`/report`、`/cancel`、`/session` 观察服务端事实；将 session ID 交给 `cd backend && uv run python -m cli dump <session-id>` 做进一步定位。

`/connect <api-url>` 先检查候选 URL 的 `/health`。失败时保留当前连接和会话上下文。
