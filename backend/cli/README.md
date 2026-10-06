# DR4A 调试 CLI

`python -m cli` 是给 Agent 和开发者的后端调试入口。它不是用户研究入口：Clarify 的多轮对话由 HTTP/API 或前端处理；CLI 只消费已经冻结的 ResearchBrief。

当前正在迁移到 [mono CLI 契约](../../docs/mono/api-contract.md#5-cli-契约)。`dump` 已读取新的 owner-scoped Run Checkpoint；`phase` 已使用正式执行器/合并，仅注册已有的 plan worker。其他 phase 明确报未配置；`run/ingest/search` 仍使用旧执行链，尚不能用来验收 mono 流水线。

在 `backend/` 目录中运行：

```bash
python -m cli doctor --json
python -m cli run --brief frozen-brief.json --json
python -m cli phase plan --state plan-state.json --seed 42 --json
python -m cli dump <session-id> --json
```

默认是 fake 模式：全内存、无外部依赖，适合快速、可复现的调试。`--seed N` 固定 fake 输出；需要真实 LLM、检索与持久化时显式传 `--real`，并先运行 `doctor`。

## 命令

| 命令 | 用途 |
|---|---|
| `doctor` | 检查真实环境的 env、PostgreSQL、Milvus、MinIO 和模型权重。 |
| `run --brief FILE` | 用完整冻结 Brief 跑 pipeline 到报告。 |
| `phase PHASE --state FILE` | 用快照状态只执行一个 phase。 |
| `dump SESSION_ID` | 从 PostgreSQL 读取当前 Run 的最新 seq，不按阶段倒序。 |
| `ingest PDF` / `search QUERY` | 独立调试知识库入库与检索。 |

`run` 的 Brief 必须含 ResearchBrief 的 10 个字段，且 `task_type` 为 `idea_exploration`、`method_differentiation`、`evaluation_design` 或 `reviewer_response`。CLI 会在调用 Agent 前拒绝不完整输入。

## phase 输入前置

`phase` 要求严格完整 mono PipelineState（schema_version=1），包括全部空输出字段；不接受旧版局部dict。`state.phase` 必须与命令相同，来源/config/Brief hash与事实回链必须有效。阶段前置复用正式 PhaseInput；当前仅 plan worker 可执行。

| phase | 最低输入 |
|---|---|
| `plan` | 完整 Brief、冻结来源与运行配置 |
| `research` | 完整五章计划/来源与事实 map（worker待接入） |
| `analyze` | 五章计划/分析输入；Observation允许空但不能缺key（worker待接入） |
| `write` | plans/事实/五章coverage，空证据必须有明确缺口（worker待接入） |
| `review` | 完整同版 draft_sections/bindings及事实回链（worker待接入） |

成功时，`phase --json` 输出完整 post-state、events，以及顶层 `state_delta`。缺少前置或状态阶段不匹配会以退出码 2 退出，并在 stderr 指出原因。

`phase plan` fake 使用明确受控计划经过同一个正式worker；`--real`使用配置的模型，并验证snapshot中的模型/prompt版本。仅执行/合并当前阶段，phase保持plan，不创建Session/Report、不连接PG或更新原Run。真实debug用量单列在 `debug_usage`，不伪装原Run的持久预算。私有来源/KB授权尚未接入时，real明确拒绝。

使用冻结Brief生成完整输入并验证真正CLI子进程：

```bash
uv run python -m scripts.verify_cli_plan --brief frozen-brief.json
uv run python -m scripts.verify_cli_plan --brief frozen-brief.json --real
```

`--real`会消耗模型tokens，`--record NEW_FILE`可保存输入与完整结果，文件存在时拒绝覆盖。该入口只验证plan，不是持久Run或完整研究验收。

## 输出与退出码

- `--json`：stdout 为一个可解析的 JSON 对象；日志和错误走 stderr。
- `--verbose`：LLM prompt/response 走 stderr。
- `--quiet`：`run` 时省略事件。
- 退出码：`0` 成功、`1` 运行失败、`2` 用法/调试输入错误、`3` 环境错误。

正式契约见 [`specs/002-cli/contracts/cli.md`](../../specs/002-cli/contracts/cli.md)；冻结 Brief 与报告约定见 `specs/001-deep-research-agent/`。

## mono 状态读取

```bash
uv run python -m cli dump SESSION_UUID --json
uv run python -m cli dump SESSION_UUID --owner OWNER_UUID --json
```

开发环境默认使用固定开发 owner；`--owner` 指定已有用户，production 必须显式提供。dump 不创建用户、不迁移数据库、不领取或恢复 Run。没有该 owner 的会话返回 `session_not_found`；会话尚无 Run 返回 `checkpoint_not_found`；旧 schema 明确失败，不退回旧快照。成功输出 `state`、`phase`、`run_id`、`checkpoint_seq`，返工后仍读取最新 seq。保存JSON结果中的 `state` 字段作为phase输入，并保持阶段标记与命令一致。

JSON 模式错误也返回单个对象，`error` 包含 code/message/details/retryable/request_id；成功 error 为 null。未知 SDK 异常不打印原异常正文。
