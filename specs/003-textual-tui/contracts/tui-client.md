# pi-tui 客户端边界

服务端 API 语义以 [Research API 契约](../../../docs/contracts/research-api.md) 为唯一来源；本文件只规定当前匿名开发客户端如何消费接口。正式认证仍由权威契约定义，但不属于本 feature。

| TUI 动作 | HTTP 调用 | 成功后客户端动作 |
| --- | --- | --- |
| 初始 query | `POST /research` | 追加 `ask` 或 `confirm` 响应 |
| Clarify 回答 | `POST /research/{id}/messages` | 追加响应，等待下一步 |
| 接受 Brief | `POST /research/{id}/confirm {accepted:true}` | 仅 `202 ready` 且有 `sse_url` 后连接 SSE |
| 要求修改 | `POST /research/{id}/confirm {accepted:false,feedback}` | 追加新的 `ask` 响应 |
| 运行观察 | `GET sse_url` | 按收到顺序追加事件 |
| 调试命令 | status/report/cancel 对应研究端点 | 将实际响应追加到时间线 |
| `/connect` | 候选 URL 的 `GET /health` | 成功后切换并清空会话 |

`401`、未知状态、结构异常、网络错误和 SSE 中断都是时间线诊断条目，不转换为成功状态。当前客户端不会调用 `/auth/register` 或 `/auth/login`。
