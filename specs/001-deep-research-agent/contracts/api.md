# 接口契约：Auth + Research + Knowledge Base

> Phase 1 输出。系统对用户暴露的接口。数据级契约（ResearchBrief 10 字段、报告骨架）
> 见 `docs/contracts/`，此处定义传输层接口。

## 1. 认证

### POST /auth/register

`{ email, password }` → 201（密码 bcrypt/argon2 哈希，禁止明文）。

### POST /auth/login

`{ email, password }` → `{ access_token }`（JWT / cookie）。

其余受保护接口：中间件从 cookie/token 解出 user_id，校验归属。

## 2. 研究会话

### POST /research

`{ query, task_type?, sources? }` → `{ session_id, status: "clarify" }`（不返回 sse_url，SSE 只在 ready 后才有）。

- 只建 session（SessionState），**不跑 clarify**。
- `task_type` / `sources` 缺省或 `auto` 时由系统/澄清决定。

### POST /research/{session_id}/messages

`{ content }` → 推进**一轮** clarify：

- `status=ask` → `{ status: "ask", questions, missing_fields }`（反问，等下一轮）
- `status=ready` → 冻结 brief + 后台 spawn orchestrator → `{ status: "ready", sse_url }`

（clarify 是逐轮交互，每轮一条 message；不是一次性 /clarify 调用。）

### GET /research/{session_id}/events（SSE，`text/event-stream`）

事件类型：

| event | payload | 说明 |
|---|---|---|
| `phase` | `{ phase, message }` | 阶段切换（plan/research/analyze/write/review/done） |
| `progress` | `{ section_id, stage, results?, chart? }` | 章节级进度与结构化结果（results 列表 / chart 对象，非任意 JSON） |
| `rework` | `{ issue_id, action, target }` | 回流路由（action 来自政策表，非 LLM） |
| `error` | `{ code, message }` | 失败信息（可恢复/不可恢复） |
| `done` | `{ final_report_url }` | 完成，指向最终报告 |

（SSE 端点用 cookie；EventSource 不能带自定义 header。）

### GET /research/{session_id}/report

返回最终报告（统一骨架 + 任务专属第 3 节，见 `docs/contracts/report-skeleton.md`）。

### GET /research/{session_id}

返回任务当前状态；中断恢复时从 `phase_snapshots` **同 phase 取最新一行**。

### POST /research/{session_id}/cancel

`{ }` → 设置进程内取消标志（orchestrator 每 0.5s 轮询，若取消则停）。

## 3. 知识库

### POST /knowledge-base/documents

multipart 上传 PDF → `202 { document_id, status: "processing" }`。入库走 `asyncio.to_thread` 后台流水线。

### GET /knowledge-base/documents/{document_id}

→ `{ status: processing/done/failed, progress }`（进度持久化于 documents 表，轮询读）。

### GET /knowledge-base/documents

列表。

### DELETE /knowledge-base/documents/{document_id}

删除（顺序 PG → Milvus → MinIO，避免孤儿数据）。

### POST /knowledge-base/search

`{ query, kb_id, top_k=20, rerank=true }` → `{ chunks: [...] }`（走 RetrievalPort：embed → hybrid → rerank）。

## 约束

- 幂等：相同 `session_id` / `document_id` 的重复请求不重复执行。
- 检索预算与回流轮次在服务端强制上限（见 data-model.md 的 run_metadata）。
- 崩溃恢复：启动时扫 `processing` 文档 → 标 failed（或 pending 重入队）。
