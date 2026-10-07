# DR4A 调试 CLI

`python -m cli` 是给 Agent 和开发者的后端调试入口。它不是用户研究入口：Clarify 的多轮对话由 HTTP/API 或前端处理；CLI 只消费已经冻结的 ResearchBrief。

当前正在迁移到 [mono CLI 契约](../../docs/mono/api-contract.md#5-cli-契约)。`dump` 已读取新的 owner-scoped Run Checkpoint；`phase` 已使用正式执行器/合并，注册 plan 和 research worker。`run --real`已使用正式冻结/账本/Driver/限定Runner，但入口仍仅装配 plan；缺少其他worker时明确failed并保留检查点，不能验收完整研究。默认fake run仍是旧链路，JSON标记legacy_fake；ingest/search也仍待迁移。

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

`run --real`的Brief是严格mono ResearchBrief十字段，全部为字符串（assumptions允许空），task_type仅idea_exploration/method_differentiation/evaluation_design。旧fake仍接受历史列表格式/四类型，但不代表mono报告能力。CLI会在调用Agent前拒绝不完整输入。

真实run支持`--owner UUID`（production必需）、`--sources papers,web`、重复`--kb UUID`；KB选择必须包含knowledge_base类别，当前未配置的KB授权明确拒绝。需提前显式迁移到mono schema并准备MinIO bucket；run不会自动迁移历史数据库或建bucket。SIGINT/SIGTERM只在同事务确认仍持有租约时请求取消，不取消别的worker已领取的Run。失败JSON仍给session_id/run_id/phase/checkpoint_seq，可接dump继续检查；--quiet省略events，--seed不影响real。

## phase 输入前置

`phase` 要求严格完整 mono PipelineState（schema_version=1），包括全部空输出字段；不接受旧版局部dict。`state.phase` 必须与命令相同，来源/config/Brief hash与事实回链必须有效。阶段前置复用正式 PhaseInput；当前 plan 和 research 可执行。

| phase | 最低输入 |
|---|---|
| `plan` | 完整 Brief、冻结来源与运行配置 |
| `research` | 完整五章计划/来源与事实 map |
| `analyze` | 五章计划/分析输入；Observation允许空但不能缺key（worker待接入） |
| `write` | plans/事实/五章coverage，空证据必须有明确缺口（worker待接入） |
| `review` | 完整同版 draft_sections/bindings及事实回链（worker待接入） |

成功时，`phase --json` 输出完整 post-state、events，以及顶层 `state_delta`。缺少前置或状态阶段不匹配会以退出码 2 退出，并在 stderr 指出原因。

执行中发生已分类的业务/依赖错误或 timeout 时，非零退出仍返回最后成功合并的 `state`、`state_delta`、events、`debug_usage` 和 `failed_unit_id`；失败单元的未合并结果不计入 state。这只是本地调试状态，不是 PG checkpoint，也不表示该阶段完成。预检拒绝或未分类异常不保证有 state；未分类 SDK 异常正文仍隐藏。`debug_usage.search_outcomes` 记录检索源、尝试次数、状态及安全错误码，不输出供应商异常正文或凭据。

`phase plan` fake 使用明确受控计划经过同一个正式worker；`--real`使用配置的模型，并验证snapshot中的模型/prompt版本。仅执行/合并当前阶段，phase保持plan，不创建Session/Report、不连接PG或更新原Run。真实debug用量单列在 `debug_usage`，不伪装原Run的持久预算。私有来源/KB授权尚未接入时，real明确拒绝。

使用冻结Brief生成完整输入并验证真正CLI子进程：

```bash
uv run python -m scripts.verify_cli_plan --brief frozen-brief.json
uv run python -m scripts.verify_cli_plan --brief frozen-brief.json --real
```

`--real`会消耗模型tokens，`--record NEW_FILE`可保存输入与完整结果，文件存在时拒绝覆盖。该入口只验证plan，不是持久Run或完整研究验收。

## research 原文探针

```bash
uv run python -m cli phase research --state research-state.json --real --json
uv run python -m scripts.verify_research_phase --state research-state.json --real --record NEW_FILE
```

真实模式使用公开搜索、受限下载器和 MinIO。`config.versions.parser_version=dr4a-html-v1` 使用 HTML Parser；`dr4a-mineru-4.0.10-standard-v1` 使用本地 PDF Parser，需要安装可选 `parser` 依赖并通过 `MINERU_MODELS_DIR` 指定已显式准备的权重目录。需提前创建配置的 bucket；CLI 不自动建 bucket 或下载模型。PDF 模式目前只接收 PDF，macOS 子进程使用系统级禁止网络访问约束，Linux 隔离未接入时明确拒绝；KB 尚未接入。fake research 返回空搜索并保留 Gap，不制造原文或观察。

此入口不写 PG、不领取租约、不修改原 Run 的预算或阶段。`debug_usage` 单列本次模型用量、搜索尝试和 Fetch 调用次数；Fetch 次数包含被安全检查拒绝的调用，不等同于成功下载。原文对象保存在独立随机 `artifact_scope` 下，不借用输入 Run 的命名空间，执行后保留以便审计。含既有事实的快照可能因原文对象范围不兼容而拒绝合并，不能据此宣称持久 Run 恢复通过。

探针从 MinIO 读取并重解析原文，核对 Evidence hash、位置与摘录范围。没有原文 Evidence 或没有真实论文来源时，真实验收非零退出；可保存失败/Gap 记录，不把搜索摘要视为原文。完整 T028 还要求真实 plan 输出、论文 PDF 及适用的数值观察。

`verify_cli_plan --sources papers` 可冻结仅论文来源，默认仍为 `papers,web`；`PARSER_VERSION` 在生成输入时进入冻结配置，后续 phase 不静默切换 parser。用 plan 结果中的完整 state 作为下一阶段输入，仅按正式阶段前置将 phase 标记改为 research，保留 Brief/config/计划和事实。独立 phase 不推进持久 Run。

Observation 的表格 `raw_value` 必须来自完整单元格，不能截取系数、指数或不确定性。表格 HTML 的上标用 `^`、下标用 `_` 表示；含糊科学计数法保留原文并保持数值 null，不自动修补 OCR 拆列，也不据此声称行列归属/比较条件均已验证。

## 输出与退出码

- `--json`：stdout 为一个可解析的 JSON 对象；日志和错误走 stderr。
- `--verbose`：操作元信息走 stderr，不打印 prompt/response 正文。
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

## 活 HTTP Run 验证

`run`仍在迁移时，不要用其旧结果验收mono。先通过HTTP Clarify探针审阅并明确确认Brief，再观察已接受的Run：

```bash
uv run python -m scripts.verify_run_http --session SESSION_UUID --model-mode real
```

仅传已有`--session`时只订阅SSE并核对最新HTTP状态/报告，不直接执行或创建Run。`--action cancel`或`--action resume`才发送对应控制请求；resume需要failed且resume_allowed。`--url`可指定后端，`--timeout`限制整个过程。`--model-mode controlled`用于明确受控测试，不代表真实研究能力通过。当前生产完整worker尚未组合，缺能力时超时/失败是有效诊断，不会自动退回fake。

也可以用同一探针创建/澄清：`--query "公开研究问题"`，随后用返回的Session UUID加`--answers-file answers.json`继续。系统到confirm后需审阅完整Brief，再显式提供`--approve-file approval.json`；格式复用Clarify探针的session_id/brief_version/research_brief。未提供确认文件不会启动Run，不能与cancel/resume action混用。
