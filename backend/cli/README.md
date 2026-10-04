# DR4A 调试 CLI

`python -m cli` 是给 Agent 和开发者的后端调试入口。它不是用户研究入口：Clarify 的多轮对话由 HTTP/API 或前端处理；CLI 只消费已经冻结的 ResearchBrief。

在 `backend/` 目录中运行：

```bash
python -m cli doctor --json
python -m cli run --brief frozen-brief.json --json
python -m cli phase review --state review-ready.json --seed 42 --json
python -m cli dump <session-id> --json
```

默认是 fake 模式：全内存、无外部依赖，适合快速、可复现的调试。`--seed N` 固定 fake 输出；需要真实 LLM、检索与持久化时显式传 `--real`，并先运行 `doctor`。

## 命令

| 命令 | 用途 |
|---|---|
| `doctor` | 检查真实环境的 env、PostgreSQL、Milvus、MinIO 和模型权重。 |
| `run --brief FILE` | 用完整冻结 Brief 跑 pipeline 到报告。 |
| `phase PHASE --state FILE` | 用快照状态只执行一个 phase。 |
| `dump SESSION_ID` | 从 PostgreSQL 读取真实运行的最新 phase snapshot。 |
| `ingest PDF` / `search QUERY` | 独立调试知识库入库与检索。 |

`run` 的 Brief 必须含 ResearchBrief 的 10 个字段，且 `task_type` 为 `idea_exploration`、`method_differentiation`、`evaluation_design` 或 `reviewer_response`。CLI 会在调用 Agent 前拒绝不完整输入。

## phase 输入前置

`phase` 不会替生产状态机做业务决策；它只是调试入口的防呆层。`state.phase` 必须与命令的 phase 相同，且输入至少包含非空的下列字段：

| phase | 最低输入 |
|---|---|
| `plan` | `research_brief` |
| `research` | `research_brief`、`section_plans` |
| `analyze` | `section_plans`、`quantitative_observations` |
| `write` | `research_brief`、`section_plans`、`claims`、`evidence` |
| `review` | `draft_claim_bindings`、`claims`、`evidence`、`sources` |

成功时，`phase --json` 输出完整 post-state、events，以及顶层 `state_delta`。缺少前置或状态阶段不匹配会以退出码 2 退出，并在 stderr 指出原因。

## 输出与退出码

- `--json`：stdout 为一个可解析的 JSON 对象；日志和错误走 stderr。
- `--verbose`：LLM prompt/response 走 stderr。
- `--quiet`：`run` 时省略事件。
- 退出码：`0` 成功、`1` 运行失败、`2` 用法/调试输入错误、`3` 环境错误。

正式契约见 [`specs/002-cli/contracts/cli.md`](../../specs/002-cli/contracts/cli.md)；冻结 Brief 与报告约定见 `specs/001-deep-research-agent/`。
