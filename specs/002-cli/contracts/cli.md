# 接口契约：后端调试 CLI

> Phase 1 输出。CLI 对 Agent / 开发者暴露的接口。HTTP 传输层契约见 001 号的 `contracts/api.md`；此处定义 CLI 命令集与输出契约。

## 1. 命令集

所有命令经 `python -m cli <cmd>` 调用。通用 flag：

| flag | 作用 |
|---|---|
| `--json` | stdout 输出单个 JSON 对象（结果本体） |
| `--verbose` | 每个 LLM 调用的 prompt + response 打到 stderr。fake 模式打的是 phase prompt + seeded 合成 response（验「prompt 结构对不对」）；real 模式打真实 prompt/response（验「模型行为对不对」）——两者是不同的调试信号，勿混 |
| `--quiet` | 只打最终报告，压掉进度噪音（`run` 有意义，其余命令无进度噪音时为 no-op） |
| `--real` | 关闭默认 fake，启用真实依赖（deepseek / arxiv / bocha / postgres / milvus） |

## 2. 各命令

### `doctor [--json]`

体检 PG / Milvus / MinIO / 模型权重 / env。逐项 PASS/FAIL；任一 FAIL → 退出码 3。天然是 real 检查，无 `--fake`。

### `run --brief f.json [--real] [--seed N] [--json] [--verbose] [--quiet]`

从冻结 ResearchBrief 跑：pipeline → report。

- `--brief f.json`：必填。必须是完整的 ResearchBrief 10 字段，`task_type` 必须是受支持枚举。
- Clarify 不是 CLI 子命令：多轮对话由 HTTP/API 或前端完成；CLI 不用罐头答案伪造一段 Clarify。
- `--seed N`：fake 输出的种子（同 seed 同结果、异 seed 异结果）。
- `--real`：会往 PG 写 session / brief / snapshots，有持久化副作用；默认 fake 全内存、无副作用。

### `phase <phase> --state state.json [--real] [--seed N] [--json] [--verbose]`

只跑一个 pipeline phase。`phase ∈ plan / research / analyze / write / review`。

- `--state state.json`：喂一份 PipelineState，隔离「是哪个 phase 坏了」。state 内的 `phase` 必须与命令相同。
- CLI 调试层会检查该 phase 的最低前置字段；不满足时以退出码 2 拒绝，避免空 state 伪装为成功。
- 复用 orchestrator 执行单 phase 的那段（同一 state 切分、事件发射、结果合并），不另起一套 agent 调用。

| 要运行的 phase | 最低有效输入 |
|---|---|
| `plan` | `research_brief` |
| `research` | `research_brief` + `section_plans` |
| `analyze` | `section_plans` + `quantitative_observations` |
| `write` | `research_brief` + `section_plans` + `claims` + `evidence` |
| `review` | `draft_claim_bindings` + `claims` + `evidence` + `sources` |

### `dump <session_id> [--json]`

从 `phase_snapshots` 读最新 state 打印（「为什么卡在这」看这个）。**唯一 real-mode 命令**：读的是 PG 里的快照，需真实 backend 先跑出过快照；fake 模式下无数据。

### `ingest <pdf> [--kb default] [--json]`

单独入库一个 PDF → `{document_id, status}`。

### `search <query> [--kb default] [--json]`

单独检索 → `{chunks: [...]}`。

## 3. 输出契约

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 研究失败（pipeline / agent 出错） |
| 2 | 用法错误（参数错、命令不存在） |
| 3 | 环境错误（doctor 不通过、依赖缺失） |

### stdout（--json 时）

单个 JSON 对象：

```json
{
  "status": "ok" | "failed" | "usage_error" | "env_error",
  "final_report": {},     // run 成功时
  "state": {},            // phase 成功后的完整状态
  "state_delta": {},      // phase 后发生变化的顶层字段
  "error": "",            // 失败时
  "events": []            // 可选，事件流；--quiet 时省略（缓冲整流可能很大）
}
```

默认（不带 `--json`）为人类可读排版。

### stderr

错误 + 日志，一行一条、带时间戳。`--verbose` 时每个 LLM 调用的 prompt + response 也打到这里（fake 为合成 response、real 为真实 response），不污染 stdout。

## 约束

- 确定性：同一 `--seed N` 输入两次 fake 运行 stdout 完全一致。
- 非交互：任何命令都不阻塞等待人工输入；CLI 只消费 Clarify 已冻结的 Brief。
- 幂等：`dump`/`ingest`/`search` 可重复执行，无副作用。
- 单套实现：`--seed` 确定性加进 `infrastructure/fake.py`（测试与 CLI 共享同一套 fake），CLI 只注入 seed，不另造一套。
