# 接口契约：Research API + SSE

> Phase 1 输出。系统对用户暴露的接口。数据级契约（ResearchBrief 10 字段、报告骨架）
> 见 `docs/contracts/`，此处定义传输层接口。

## 1. 提交研究任务

`POST /research`

请求体：

```json
{
  "query": "自然语言研究请求",
  "task_type": "idea_exploration | method_differentiation | evaluation_design | reviewer_response | auto"
}
```

- `task_type` 缺省或 `auto` 时由系统判断。
- 返回 `{ "session_id": "...", "status": "clarify" }`。

## 2. 澄清交互

`POST /research/{session_id}/clarify`

- 请求体：用户对澄清问题的回答。
- 响应：`status`（ask / confirm / ready）、`missing_fields`、`questions`、`brief_patch`、`assumptions`。
- 当 `status = ready` 时，ResearchBrief 冻结，进入调研。

## 3. 获取冻结的 ResearchBrief

`GET /research/{session_id}/brief`

返回 ResearchBrief（10 字段，见 `docs/contracts/researchbrief.md`）。

## 4. 进度推送（SSE）

`GET /research/{session_id}/events`（`text/event-stream`）

事件类型：

| event | payload | 说明 |
|---|---|---|
| `phase` | `{ phase, message }` | 阶段切换（clarify/planning/research/analyze/write/review/done） |
| `progress` | `{ section_id, stage, detail }` | 章节级进度与增量结果 |
| `rework` | `{ issue_id, required_action, target }` | 审阅返工路由 |
| `error` | `{ code, message }` | 失败信息（可恢复/不可恢复） |
| `done` | `{ final_report_url }` | 完成，指向最终报告 |

## 5. 获取最终报告

`GET /research/{session_id}/report`

返回最终报告（统一骨架 + 任务专属第 3 节，结构见 `docs/contracts/report-skeleton.md`）。

## 6. 恢复

`GET /research/{session_id}`

返回任务当前状态；若中断，可从最近 checkpoint 恢复，不重复已完成的检索/写入。

## 约束

- 所有接口幂等：相同 `session_id` 的重复请求不重复执行已完成的阶段。
- 检索预算与回流轮次在服务端强制上限（见 data-model.md 的 run_metadata）。
