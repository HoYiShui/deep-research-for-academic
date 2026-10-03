# 接口契约：后端调试 CLI

> Phase 1 输出。CLI 对 Agent / 开发者暴露的接口。HTTP 传输层契约见 001 号的 `contracts/api.md`；此处定义 CLI 命令集与输出契约。

## 1. 命令集

所有命令经 `python -m cli <cmd>` 调用。通用 flag：

| flag | 作用 |
|---|---|
| `--json` | stdout 输出单个 JSON 对象（结果本体） |
| `--verbose` | 每个 LLM 调用的 prompt + response 打到 stderr。fake 模式打的是 phase prompt + seeded 合成 response（验「prompt 结构对不对」）；real 模式打真实 prompt/response（验「模型行为对不对」）——两者是不同的调试信号，勿混 |
| `--quiet` | 只打最终报告，压掉进度噪音（`run` 有意义，其余命令无进度噪音时为 no-op） |
| `--no-fake` | 关闭默认 fake，启用真实依赖（deepseek / arxiv / bocha / postgres / milvus） |

## 2. 各命令

### `doctor [--json]`

体检 PG / Milvus / MinIO / 模型权重 / env。逐项 PASS/FAIL；任一 FAIL → 退出码 3。天然是 real 检查，无 `--fake`。

### `run [query] [--brief-file f.json] [--answers a.json] [--no-fake] [--seed N] [--json] [--verbose] [--quiet] [--max-iterations N]`

跑完整一条：clarify → pipeline → report。

- `query` 与 `--brief-file` **二选一、至少一个**：给了 query 走 clarify；给了 `--brief-file` 跳过 clarify，直接读冻结 brief 进 pipeline。
- `--answers a.json`：clarify 自动用罐头答案（每行/每项一条），不交互。
- `--seed N`：fake 输出的种子（同 seed 同结果、异 seed 异结果）。
- `--max-iterations N`：回流迭代上限。
- `--no-fake`（real 模式）：会往 PG 写 sessions / briefs / snapshots，有持久化副作用；默认 fake 全内存、无副作用。

### `slice <phase> [--input state.json] [--no-fake] [--seed N] [--json] [--verbose]`

只跑单个 phase 的 agent。`phase ∈ plan / research / analyze / write / review`。

- `--input state.json`：喂一份罐头 PipelineState，隔离「是哪个 agent 坏了」。
- 复用 orchestrator 执行单 phase 的那段（同一 state 切分、事件发射、结果合并），不另起一套 agent 调用。

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
  "error": "",            // 失败时
  "events": []            // 可选，事件流；--quiet 时省略（缓冲整流可能很大）
}
```

默认（不带 `--json`）为人类可读排版。

### stderr

错误 + 日志，一行一条、带时间戳。`--verbose` 时每个 LLM 调用的 prompt + response 也打到这里（fake 为合成 response、real 为真实 response），不污染 stdout。

## 约束

- 确定性：同一 `--fake --seed N` 输入两次运行 stdout 完全一致。
- 非交互：任何命令都不阻塞等待人工输入；clarify 用 `--brief-file` 或 `--answers` 绕过。
- 幂等：`dump`/`ingest`/`search` 可重复执行，无副作用。
- 单套实现：`--seed` 确定性加进 `infrastructure/fake.py`（测试与 CLI 共享同一套 fake），CLI 只注入 seed，不另造一套。
