# pi-tui 运行时数据模型

这些 TypeScript 对象只在当前 TUI 进程内存在，不替代服务端 SessionState，也不写入文件。

## ApiError

- `statusCode`: HTTP 状态码；网络错误没有该值。
- `code`: 服务端错误码或客户端诊断码。
- `message`: 面向开发者的可读错误。

## ResearchContext

- `sessionId`: 当前服务端会话 UUID，可为空。
- `status`: 最近观察到的 `ask`、`confirm`、`ready`、`running`、`done` 等状态。
- `phase`: 最新 SSE/状态响应的 phase，可为空。
- `sseUrl`: 仅 `ready` 响应返回后保存。
- `researchBrief`: `confirm` 下服务端返回的完整 Brief。

## TimelineEntry

- `sequence`: 本次运行内递增顺序。
- `kind`: `system`、`user`、`clarify`、`brief`、`pipeline`、`report`、`error` 或 `command`。
- `content`: 用 pi-tui 组件渲染的可读内容；保留服务端关键事实。

## 关键状态转换

1. 初始状态：匿名开发模式，composer 接受 query。
2. 创建/回答 Clarify：更新 `sessionId` 和 `status` 为 `ask` 或 `confirm`。
3. `confirm`：保存 Brief，等待用户明确动作，不能连接 SSE。
4. 接受 Brief：仅 `202 ready + sse_url` 时保存地址并启动 SSE reader。
5. SSE `done`：保留事件并读取报告；`error`/断开不伪装成完成。
6. `/connect` 成功或 `/new`：清空活动会话；健康检查失败则保留旧会话。
