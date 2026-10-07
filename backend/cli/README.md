# DR4A 调试 CLI

`python -m cli` 是给 Agent 和开发者的后端调试入口。它不是用户研究入口：Clarify 的多轮对话由 HTTP/API 或前端处理；CLI 只消费已经冻结的 ResearchBrief。

当前正在迁移到 [mono CLI 契约](../../docs/mono/api-contract.md#5-cli-契约)。`dump` 已读取新的 owner-scoped Run Checkpoint；`phase` 已使用正式执行器/合并，注册 plan 和 research worker。`run --real`复用TUI的公开来源plan/research、冻结/账本/Driver/限定Runner；缺少analyze/write/review时明确failed并保留检查点，不能验收完整研究。默认fake run仍是旧链路，JSON标记legacy_fake；ingest/search也仍待迁移。

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
| `doctor` | 只读检查配置、PG连接/迁移标记、MinIO bucket；默认另查Milvus连接/本地模型文件，不能证明推理或完整Pipeline可用。 |
| `run --brief FILE` | 跳过 Clarify 执行冻结 Brief；当前 real 装配 plan/research，不能产出完整报告。 |
| `phase PHASE --state FILE` | 用快照状态只执行一个 phase。 |
| `dump SESSION_ID` | 从 PostgreSQL 读取当前 Run 的最新 seq，不按阶段倒序。 |
| `ingest PDF` / `search QUERY` | 独立调试知识库入库与检索。 |

`run --real`的Brief是严格mono ResearchBrief十字段，全部为字符串（assumptions允许空），task_type仅idea_exploration/method_differentiation/evaluation_design。旧fake仍接受历史列表格式/四类型，但不代表mono报告能力。CLI会在调用Agent前拒绝不完整输入。

真实run支持`--owner UUID`（production必需）、`--sources papers,web`、重复`--kb UUID`；KB选择必须包含knowledge_base类别，当前未配置的KB授权明确拒绝。需提前显式迁移到mono schema并准备MinIO bucket；run不会自动迁移历史数据库或建bucket。SIGINT/SIGTERM只在同事务确认仍持有租约时请求取消，不取消别的worker已领取的Run。失败JSON仍给session_id/run_id/phase/checkpoint_seq，可接dump继续检查；--quiet省略events，--seed不影响real。

CLI只启动限定owner+本Run的扫描/执行器，不启动HTTP全库维护器；即使进程环境设置了`DR4A_DEBUG_RUNNER=true`也不会附带开启全库HTTP执行。其它Run的ready排队、失租或取消由其服务器/维护进程处理，不由此次CLI命令收尾。

real run必须显式配置已支持的parser版本；不沿用`unconfigured`或静默切换HTML/PDF。未知parser或未准备PDF权重时退出3，不冻结Brief/创建Run、不请求模型。HTML公开来源模式示例（会调用收费模型/搜索，需已有mono数据库与MinIO bucket）：

```bash
PARSER_VERSION=dr4a-html-v1 uv run python -m cli run --brief frozen-brief.json --real --json
```

PDF模式用`PARSER_VERSION=dr4a-mineru-4.0.10-standard-v1`，另需已准备的`MINERU_MODELS_DIR`；格式/平台限制同下文research原文探针。CLI结果events包含本进程query/section进度；HTTP只会轮询该Run持久phase/done，不共享CLI瞬态队列。失败后可dump已提交research事实，不能把未配置后续阶段当成功报告。

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

单次Ctrl+C中断`phase`时，正常收尾关闭本地工具，并以退出1/`phase_interrupted`返回最后已合并的本地state；未完成单元不合并。它不发送HTTP cancel、不修改输入文件，也不取消输入state对应的持久Run。若要停止TUI中的研究，使用TUI `/cancel`。重跑独立phase可能再次收费，不是持久Checkpoint恢复。

运行中定位卡点可加`--verbose`：stderr实时输出`debug_unit`的`stage=started/merged/failed`、phase、unit_id、章节与已合并/总单元数；不输出query、prompt、供应商正文。`merged`只表示本地state已校验合并，明确标注`persistence=local_only`，不表示PG checkpoint或获得有效证据。开始帧在调用worker前输出，已分类失败/中断无伪造merged；最终stdout仍是一个JSON对象，原events格式不变。

```bash
uv run python -m cli phase research --state research-state.json --real --json --verbose > research-result.json
```

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

此入口不写 PG、不领取租约、不修改原 Run 的预算或阶段。`debug_usage` 单列本次模型用量、搜索尝试和 Fetch 调用次数；Fetch 次数包含被安全检查拒绝的调用，不等同于成功下载。新原文与解析对象保存在独立随机 `artifact_scope` 下，不借用输入 Run 的写入命名空间，执行后保留以便审计。若重新获取的公开来源与输入快照中的 Source ID、原文哈希相同，CLI 会只读核验旧原文的字节哈希和大小，保留旧原文引用，避免仅因存储目录不同而拒绝合并；新解析对象仍留在本次调试目录。旧原文缺失或损坏会失败，来源其他不可变字段冲突仍由正式合并校验拒绝。这里仍会重新下载、解析，不是持久 Run 的缓存命中或恢复，不复用其预算。

探针从 MinIO 读取并重解析原文，核对 Evidence hash、位置与摘录范围。没有原文 Evidence 或没有真实论文来源时，真实验收非零退出；可保存失败/Gap 记录，不把搜索摘要视为原文。完整 T028 还要求真实 plan 输出、论文 PDF 及适用的数值观察。

`verify_cli_plan --sources papers` 可冻结仅论文来源，默认仍为 `papers,web`；`PARSER_VERSION` 在生成输入时进入冻结配置，后续 phase 不静默切换 parser。用 plan 结果中的完整 state 作为下一阶段输入，仅按正式阶段前置将 phase 标记改为 research，保留 Brief/config/计划和事实。独立 phase 不推进持久 Run。

research 优先使用该章去重后的 `retrieval_anchors` 建 query 单元，无 anchors 时兼容 sub_questions；纯建议章没有子问题时不启动背景检索。明确 arXiv 编号采用标准 `id_list` 定位，版本号保留。普通关键词仍走搜索；这不等于已经解决所有查询生成、相关性排序或受限追溯。

Observation 的表格 `raw_value` 必须来自完整单元格，不能截取系数、指数或不确定性。表格 HTML 的上标用 `^`、下标用 `_` 表示；含糊科学计数法保留原文并保持数值 null，不自动修补 OCR 拆列，也不据此声称行列归属/比较条件均已验证。

## 输出与退出码

- `--json`：stdout 为一个可解析的 JSON 对象；日志和错误走 stderr。
- 参数解析失败（缺必需参数、未知命令/phase/选项等）也返回退出2的标准JSON error，不回显可能含私密内容的原参数；`--help`仍是普通帮助文本、退出0，不执行命令。`--`之后的字面`--json`不是格式开关。
- `--verbose`：操作元信息走 stderr，不打印 prompt/response 正文。
- `--quiet`：`run` 时省略事件。
- 退出码：`0` 成功、`1` 运行失败、`2` 用法/调试输入错误、`3` 环境错误。

当前设计权威是 [mono CLI 契约](../../docs/mono/api-contract.md#5-cli-契约) 与 [mono 数据模型](../../docs/mono/data-model.md)。`specs/002-cli/` 和 `specs/001-deep-research-agent/` 是历史需求，冲突的实现设计不优先于 mono。

## 验证入口与历史脚本

当前公开研究调试优先运行：

```bash
uv run python -m cli doctor --scope research --debug-db --json
```

`--debug-db`只读取已有`dr4a_debug`，不会创建/迁移数据库；仅限匿名development。普通后端库去掉该参数。`--scope research`跳过暂缓的Milvus/Embedding检查；默认`all`仍检查它们。PG能够连接但缺迁移、存在未知迁移或缺必要Run表时`postgres_schema=false`，退出3；不要为了让检查变绿自动迁移恢复旧库。

`checks`是逐项bool，`scope`和`limitations`说明检查边界，失败有标准error。MinIO检查使用配置凭据读取bucket存在性，不建bucket、不试写对象；模型文件检查拒绝Hub名称和空目录，不自动下载。PASS只表示这些有限检查通过：模型/Parser实际运行、缓存写权限、Milvus schema/hybrid、完整执行器仍需另验。匿名开发不要求JWT_SECRET；需要鉴权时才检查它。

`scripts.smoke_e2e` 与 `scripts.smoke_real` 已退役，执行只输出替代入口并以退出码 2 结束，不加载 `.env`、不请求模型、不写数据库。它们旧有的自动同意默认假设、直接调用旧 Service、硬编码数据库和“全真实 E2E”声明不能用于当前验收。

- 依赖连通诊断：`uv run python -m cli doctor --json`，不收费调用模型；通过不等于模型、Parser、执行器或完整研究可用。
- Clarify HTTP：`uv run python -m scripts.verify_clarify_http --help`，支持用户明确提供回答与审阅后的确认文件；不自动确认任务书。
- Run HTTP/SSE：`uv run python -m scripts.verify_run_http --help`，已有会话的观察默认只读，取消/恢复需显式 action。
- 单阶段：上文的 `verify_cli_plan` / `verify_research_phase`；`--real` 会产生相应模型/检索调用，不把缺证据或网络失败记录为通过。

开发入口推荐 `scripts.debug_backend` 与 TUI，使用独立 `dr4a_debug` 数据库。当前本机 PostgreSQL 已恢复到新卷，原 Compose 定义仍指向损坏旧卷：**不要运行 `docker compose up postgres` 或 `services.sh restart`**。本段是当前本机交接警告，不改变 mono 的目标部署设计。

## mono 状态读取

TUI 开发启动/操作见 [TUI README](../../tui/README.md)。使用 `scripts.debug_backend` 的独立数据库时，dump 加 `--debug-db`；默认仍读取 Settings 指定的数据库。dump/phase 不自动迁移或写回会话。

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
