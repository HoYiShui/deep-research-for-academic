# Tasks：后端对齐 mono-v1

> 状态：实施中，2026-10-05。这是用户要求的直接任务拆解，不是新增设计规范。任务只在实际验证后勾选并附证据，不继承旧 tasks 的完成标记。

> 范围追加（2026-10-05）：用户要求本轮同时实现 Web 前端，参考 ChatGPT 的聊天布局，消费事件级 SSE。追加任务 T063–T066；在后端闭环后、最终回归前完成，不以“剩余 token”作为省略验收的理由。

## 1. 输入与执行约定

唯一设计输入是 [架构](../../docs/mono/architecture.md)、[数据模型](../../docs/mono/data-model.md)、[数据流](../../docs/mono/dataflow.md)、[API 契约](../../docs/mono/api-contract.md)、[运行语义](../../docs/mono/operations.md) 五份文档，简称 ARCH/MODEL/FLOW/API/OPS。验收编号 A1–A13 指 OPS §8。项目章程仍优先。

按用户授权直接拆 tasks，不新增重复的 spec/plan/data-model/contracts。现有 Spec Kit 模板要求 spec.md/plan.md，因此本目录目前**不是完整的 Spec Kit feature 包**；不要声称已执行 `/speckit-tasks`。后续若使用要求这些文件的命令，再补引用型入口与迁移计划，不复制另一套 API。CodingAgent 可直接依本文件实施。

- 保留现有 `backend/interface / application / domain/research / infrastructure` 路径；不先做目录重命名。下列新文件是建议实施落点，同职责文件拆分可调整，但交接记录须更新路径。
- 任务格式为 `Txxx [范围]`；按阶段顺序执行，阶段内按列表顺序。明确额外依赖的任务写在条目中。不标虚假的 `[P]`，不要求并行 Agent。
- 优先复用现有代码和测试；任务完成意味着实现、测试和对应验收证据均完成，不是“新增了一个类”。未知设计问题先回写受影响 mono 文档，不能在测试/CLI 中私自另定语义。
- 测试先写反例，确认失败原因与待实现行为一致，再实现。所有原有测试需逐项判断：设计未变则保留；旧语义已废弃则改断言；不能为保留旧绿灯而恢复 `clarify` status、自动冻结或摘要冒充证据。
- CLI 可以按本次授权改造为调试工具，但 `run` 仍跳过 Clarify、`phase` 仍单阶段、`dump` 仍只读；正式 Service 和 HTTP 不得降低校验来迁就调试。Clarify 用 HTTP 脚本验证，不给 CLI 加第二套会话逻辑。
- 此任务清单不授权清空现有数据库、覆盖用户成果、删除 `docs/implementation/`、上传私有资料或推送代码。迁移先备份；真实测试用隔离数据库/KB/对象前缀，清理只涉及本轮明确创建的测试资源。

### 证据与验证命令

代码中的测试/脚本使用英文。实施证据记录在 `specs/004-backend-mono-alignment/evidence/<任务或里程碑>.md`（实施时创建）：commit/代码版本、命令、依赖模式、实际结果、失败原因、request/session/run/job IDs、SQL/hash 校验、残余边界；不存密钥、token、完整私有原文。

验证分三级，必须注明，不能互相替代：

1. **确定性测试**：fake LLM/搜索/时钟，证明状态机、Schema、异常与竞争政策。
2. **真实存储/HTTP**：活 ASGI 服务，经 HTTP 客户端访问，PG/MinIO/Milvus 真依赖；指定 fake 模型时只证明相应契约，不称全 real。
3. **真实业务 E2E**：真实模型、真实原文、真实持久化与检索；保存实际来源与报告回链。HTTP E2E 不允许 import Service 直接代调。

在 `backend/` 用 `uv run pytest -q`、`uv run python -m cli doctor --json`；前者回归，后者仅依赖诊断。后续新增脚本在 `backend/scripts/`，共用 Settings/HTTP 客户端，不手写 `.env` 解析或硬编码凭据。真实脚本默认输出脱敏 JSON，非零退出码表示失败。网络故障记录为依赖故障，不能跳过后声称通过。

## 2. 阶段 0：最小实施底座

目标：仅补首个闭环需要的类型、持久化和测试装配，不等全部 Agent/KB 实现才开始验证。

设计：ARCH §3–5；MODEL §1–3/§6；API §1/§6–7；OPS §3/§7。后续各阶段继续扩展类型和迁移。

- [x] T001 [底座] 在 `backend/tests/` 与 `backend/scripts/` 审计现有验证入口，运行并记录现有 pytest 基线、当前 CLI help 与已配置依赖的 doctor；建立 `evidence/baseline.md`，区分存根/fake/真实路径，不把历史 124 通过当当前事实。证据：[baseline](evidence/baseline.md)。
- [x] T002 [底座] 在 `backend/application/settings.py`（新）、`backend/cli/env.py`、`backend/application/bootstrap.py`、`backend/.env.example` 统一 Settings：环境优先级、development/production、端点、固定版本与预算配置；校验生产鉴权强制开启，脱敏。用 `backend/tests/unit/test_settings.py` 覆盖环境覆盖、非法配置、密钥不入快照。证据：[settings](evidence/t002-settings.md)。
- [x] T003 [底座] 更新 `backend/domain/research/state.py` 与 `backend/domain/research/ids.py`：实现十字段 Brief、SourceSelection、Session/Run/Checkpoint、RunConfig/Failure 的严格 Schema、枚举和稳定 ID；补 `backend/tests/unit/test_state.py`，覆盖 string/list 错型、未知字段、hash 与 status/phase 区分。研究事实详细 Schema 在 T027，KB Schema 在 T041 补齐。证据：[完整快照](evidence/t003-checkpoints.md)、[输入记录首批](evidence/t003-input-records.md)。
  - 旧 Orchestrator/CLI 的 dataclass 已显式隔离为 `legacy_state.py`，不能进入 mono-v1 契约；T017/T021 必须迁移并移除，不代表本次底座已完成业务接入。
- [x] T004 [底座] 在 `backend/application/ports.py`、`backend/domain/ports.py` 定义本阶段 typed Service/Repository/UnitOfWork/Clock 契约，更新 `backend/infrastructure/fake.py` 与 `backend/tests/contract/test_ports.py`；同一事务句柄可跨 Repository，不让 Router 依赖 SDK。新契约测试拆分为 `test_mono_ports.py`，保留旧回归。证据：[共享事务与 typed 契约](evidence/t004-ports.md)。
- [x] T005 [底座] 新增 `backend/infrastructure/storage/migrations/0002_mono_research.sql`，实现 users/sessions/messages/briefs/research_runs/phase_snapshots/reports/tool_calls/idempotency_requests 约束；检查现有 `0001_init.sql` 的兼容与旧数据映射。更新 `backend/infrastructure/storage/migrations.py` 防多进程迁移竞态；在 `backend/tests/integration/test_mono_migrations.py` 验证旧库升级、空库创建、重复执行和失败回滚。无法无损映射的旧记录保留且明确隔离，禁止静默丢弃。证据：[真实 PG 迁移与保留](evidence/t005-migrations.md)。
- [x] T006 [底座] 重构 `backend/infrastructure/storage/postgres.py` 或其同目录拆分 Repository：owner 查询、revision CAS、幂等 reserve/重放/冲突、Session/Brief 原子提交、冻结与 Run/Checkpoint 原子创建。用 `backend/tests/integration/test_mono_transactions.py` 做真实 PG 并发/故障注入，证明半冻结不存在。依赖 T003–T005。实现拆分至 `research_postgres.py`。证据：[真实 PG Repository](evidence/t006-repositories.md)。
- [x] T007 [底座] 在 `backend/interface/deps.py`、`backend/interface/main.py`、`backend/interface/dto/` 实现服务端身份传递、固定开发 User、统一 Error/X-Request-ID、未知字段与 HTTP 状态码；组合根可注入受控模型且测试服务生命周期正确关闭。补 `backend/tests/integration/test_mono_http_errors.py`，证明跨 owner 404、生产不能匿名启动、所有校验错误形状一致。证据：[默认HTTP与事务接入](evidence/t007-t008-t011-t012-http-clarify.md)。
  - 首批已实现统一公共错误、请求 ID、未知字段拒绝、按应用实例装配与关闭、固定开发 UUID；全量232通过。证据：[HTTP 底座首批](evidence/t007-http-base.md)。仍未完成开发 User 落库、业务 owner 传递/跨 owner 404 和 mono DTO 接入；保持未勾选，旧业务端点不视为 mono-v1。
  - 第二批完成开发身份事务创建、并发/冲突保护与 owner-scoped GET SessionView，真实隔离PG+ASGI HTTP验证跨owner404、冻结后投影与只读不执行；全量242通过。证据：[身份与会话读边界](evidence/t007-identity-views.md)。开发用户初始化已可经显式 runtime.prepare 接入，但默认生产/开发组合根尚未切换新存储，默认GET因此明确503；写接口仍待接入，不勾选T007。

门：真实 PG 事务测试通过；新旧迁移边界明确；用户数据库与未提交文件未被清理。未通过不得进入会话实施。

## 3. 阶段 1：US1 创建、澄清、确认任务书

目标：真实 HTTP → 初始 ask/confirm → 多轮补充/退回 → 显式确认 → PG 冻结 Brief、ready Run 与 seq=1 Checkpoint。

设计：MODEL §2；FLOW §2；API §2.1–2.4；OPS §2–3。验收 A1–A3。此阶段允许 ready 排队而尚不执行 Pipeline，不返回虚假报告。

- [x] T008 [US1] 在 `backend/tests/integration/test_mono_clarify_http.py` 先写 HTTP 契约测试：初始不足201 ask、充分201 confirm、后续200、confirm前无Run、重复请求不增轮次/版本、旧版本与并发消息409；另写真实 PG 断点测试验证确认失败回滚。证据：[22项HTTP/真实PG反例](evidence/t007-t008-t011-t012-http-clarify.md)。
- [x] T009 [US1] 更新 `backend/domain/research/agents/architect.py`、`backend/domain/research/machine.py` 与 `backend/tests/unit/test_clarify.py`：有界 ClarifyAssessment、十字段代码校验、task_type 闭集、保守默认披露、1–2个问题；空字段/语义关键缺口不因模型说完整而通过。补充反例位于 `test_mono_clarify.py`。证据：[严格 Clarify 与纯候选](evidence/t009-t010-clarify-candidates.md)。
- [x] T010 [US1] 更新 `backend/application/session_service.py`：assess_initial/assess_round/validate_confirmation 返回候选且不保存或 spawn；实现3轮自动模型上限、上限后明确 brief_patch、accepted=false 退回、历史消息有界输入；补 `backend/tests/unit/test_session.py` 的轮次/版本/模型失败不改旧状态反例。证据：[严格 Clarify 与纯候选](evidence/t009-t010-clarify-candidates.md)。
  - 本批完成目标worker与纯候选的单测；旧装配显式隔离到LegacySessionService/legacy_clarify，不当作目标实现。T007剩余装配与T008/T011/T012必须一起完成、移除这些legacy调用路径；尚无A1–A3真实HTTP会话证明，不得以本批代替M1。
- [ ] T011 [US1] 更新 `backend/application/research_service.py`：start/message/confirm 协调幂等与 CAS、来源授权、隐私检查、唯一 Run 冻结事务；start_frozen 复用同冻结校验并记录 CLI 确认身份。冻结后不允许改任务书，不在 SessionService 偷启流程。
  - 公开来源、幂等续租/CAS/唯一冻结、start_frozen 已通过真实PG；KB ID明确404且零外部调用。KB授权/版本锁定/隐私桥接依赖T041/T050，未完成前不勾选。证据：[事务接入](evidence/t007-t008-t011-t012-http-clarify.md)。
- [x] T012 [US1] 更新 `backend/interface/dto/research.py`、`backend/interface/router/research.py`：实现 POST /research、/messages、/confirm 与 GET SessionView 的 mono DTO，传 owner/key/version，显式201/200/202；status查询由Session+Run一致投影生成，不倒序找phase。证据：[HTTP接入](evidence/t007-t008-t011-t012-http-clarify.md)。
- [x] T013 [US1] 新增 `backend/scripts/verify_clarify_http.py` 与 `backend/tests/integration/test_verify_clarify_http.py`：只走活 HTTP，支持受控回答文件及用户明确确认，不自动同意假设；可用真实模型完成至少一条多轮会话，并用只读SQL核对1Session/冻结Brief/1Run/seq=1。保存 `evidence/us1.md`，列出实际请求/响应及模型模式。证据：[活HTTP、独立进程恢复与真实模型会话](evidence/us1.md)。

里程碑 M1：T008 的确定性 HTTP/PG反例全通过，T013真实模型会话通过；重启后 GET 仍恢复 ask/confirm/ready；没有用户确认就没有 Run。这里就能用请求日志审查真实 Clarify，不必等 Web/TUI 或完整 Pipeline。

## 4. 阶段 2：US2 可观察、可取消、可恢复的 Run

目标：持久接受的 Run 被正确领取，最新 seq 可恢复，状态与 SSE 闭环；此阶段用受控阶段结果验证调度，不宣称真实研究完成。

设计：MODEL §3；FLOW §3.1/§4；API §2.5–2.6/§3/§6–7；OPS §2–4。验收 A3/A9/A10/A12。

- [x] T014 [US2] 新增 `backend/tests/integration/test_mono_run_lifecycle.py`、`test_mono_sse.py`：先测领取竞争/旧token拒写、seq最新、取消与交付竞争、迟到订阅/两订阅者广播、失败done与重连不启动任务；用真实PG和受控阶段结果。完成复核：[T014/T015验收](evidence/t014-t015-acceptance.md)。
- [x] T015 [US2] 在 `backend/infrastructure/storage/postgres.py` 实现 Run领取/续租/失租、checkpoint expected_seq提交、报告/终态原子发布、取消与显式resume、容量约束及扫描查询；模型调用不持数据库长事务。补T014故障点测试。正式实现拆至 `research_postgres.py` 与 `run_leases.py`/`run_termination.py`/`run_publication.py`，不另建事实源。完成复核：[T014/T015验收](evidence/t014-t015-acceptance.md)。
  - 以下三批记录是历史进展，不代表当前缺口。2026-10-06按本任务自身验收复核：53项生命周期/SSE/发布测试通过；默认执行器组合、活TCP和真实业务验收分别保留在T016–T022及US3/US4，不再用后续任务阻止已完成Repository任务勾选。
  - 首批PG领取/续租/owner与全局容量/完整seq提交已通过，14项真实PG反例与Fake同步接口，全量314通过；取消/恢复/报告发布/扫描/SSE尚未完成，T014/T015均不勾选。证据：[租约与Checkpoint底座](evidence/t014-t015-leases-checkpoints.md)。
  - 第二批补取消/失败终态、失租与排队超时扫描、保留原Run/预算的显式恢复、owner队列容量；真实PG验证并发恢复、取消优先、终态故障回滚、FK锁兼容和过期租约终态写入拦截。报告发布、Runner/HTTP/SSE仍待接入，不勾选。证据：[取消与恢复](evidence/t015-cancel-recovery.md)。
  - 第三批补Report+done Checkpoint+Run/Session完成态四事实原子发布，真实PG覆盖四处写入故障回滚、取消/发布竞争、旧token/租约/seq拒绝，审核正文不允许偷改。最初7项加入后全量334通过，后补4项报告反例也通过；质量门、Runner/HTTP/SSE尚未全接入。证据：[Report事务](evidence/t015-report-transaction.md)。
- [ ] T016 [US2] 新增 `backend/application/task_runner.py` 并更新 `backend/application/bootstrap.py`、`backend/interface/main.py`：单worker扫描ready、强引用与异常观察、90s租约/20s续租/5s扫描、排队超时、graceful shutdown；服务器与CLI共同遵PG容量，进程死亡研究不自动付费重跑。
  - 2026-10-07外围手动调试：默认HttpRuntime也启动维护扫描，但未显式配置executor时禁止领取ready或调用工具。无有效租约的取消可完成、过期running转failed等待显式resume；其他进程有效租约不受干扰。TUI经独立TCP验证ready→取消→PG cancelled且attempt/工具/报告均为0；完整业务组合仍未完成，不勾选。证据：[手动维护模式](evidence/t016-http-runner.md#手动调试维护模式2026-10-07)。
  - 调度核心与显式受控executor经过6项真实PG验证，全量349通过；默认HttpRuntime尚不启动，待T017正式执行器后组合，不勾选。证据：[Runner核心](evidence/t016-runner-core.md)。
  - HttpRuntime已接受显式executor factory，准备服务后启动Runner、确认提交后wake；关闭时先停止/持久化Run中断再关模型/PG。新增HTTP确认→正式Driver五阶段→原子发布→报告/迟到SSE读取受控闭环，未再复制阶段fixture。默认缺完整业务workers时不自动领取，不暗退fake；CLI run与生产组合仍待完成，不勾选。证据：[HTTP Runner组合](evidence/t016-http-runner.md)。
- [ ] T017 [US2] 更新 `backend/application/orchestrator.py`：execute_phase输入切片/白名单PhaseResult、单元验证合并、unit_manifest、完整seq快照、Machine转换；禁止空计划成功、禁止全局State交给Agent修改，终态先提交再发事件。
  - `phase_contracts.py`已实现五阶段严格读写白名单、稳定input hash及目标范围纯合并；27项新增反例、全量388通过。正式execute_phase/Worker/manifest/快照/Machine/默认Runner组合尚待接入，不勾选。证据：[阶段契约](evidence/t017-phase-contracts.md)。
  - `phase_executor.py`已补共享dispatch、缩小工具上下文和冻结hash/来源/执行身份前置，正式plan adapter经tool callback接入；9项dispatch反例通过。全量408通过后最后一项在目标集中验证；预算/cache callback与Orchestrator逐单元提交/Machine尚未组合，不勾选。证据：[dispatch](evidence/t017-phase-dispatch.md)。
  - `phase_tools.py`已把正式plan worker的LLM入口绑定真实PG预算/cache，包含模型用量与版本/来源/权限前置及共享进程信号量；13项真实PG/MinIO+受控模型反例通过。逐单元manifest/Checkpoint与Machine仍待组合，不勾选。证据：[持久phase工具入口](evidence/t017-phase-dispatch.md)。
  - 正式`RunUnitCoordinator`已实现范围校验合并、结果ContentRef/manifest、完整单元seq提交、恢复对照前后快照验证后跳过；14项真实PG/MinIO+受控模型反例通过。MODEL同步细化结果引用，缺失对象不重跑，投影失败不影响事实。单元规划/Machine/完整Driver仍待组合，不勾选。证据：[单元提交与恢复](evidence/t017-phase-dispatch.md)。
  - 稳定单元规划、typed Machine、独立阶段提交与`RunDriver`五阶段/返工循环已组合；显式TaskRunner连接、取消竞争、完整发布事实检查、同Run预算/计时、单元/阶段间独立SIGKILL恢复已验证。32项真实PG/MinIO+受控业务输出目标集通过；生产质量门/真实业务workers及默认HTTP组合仍待实施，不勾选。证据：[Driver与进程恢复](evidence/t017-phase-dispatch.md)。
- [ ] T018 [US2] 更新 `backend/application/sse.py`、`backend/domain/research/events.py`：每订阅独立有界队列、bootstrap竞态、心跳、slow consumer、JWT过期、phase/progress/rework/error/done统一帧；为CLI持租Run轮询PG当前投影，不假装共享跨进程内存队列。
  - 2026-10-07外围调试：正式RunDriver补协调器生成的query_started/query_completed/section_completed；完成帧在Checkpoint提交后，开发DebugExecution接实际EventBus。受控worker+真实PG/MinIO/独立TCP证明事件回查seq，失败单元无完成、已提交单元resume不重复执行/投影；不改变Agent/API Schema。JWT及其它完整事件仍未完成，不勾选。证据：[逐query进度](evidence/t018-sse-core.md#2026-10-07开发run逐query进度)。
  - 默认HTTP已使用`run_sse.py`/`run_events.py`严格事件、每订阅队列与PG bootstrap/poll；全量361通过。旧CLI事件隔离，实时Orchestrator发布/JWT截止传递/独立TCP尚待接入，不勾选。证据：[SSE核心](evidence/t018-sse-core.md)。
- [ ] T019 [US2] 更新 `backend/application/research_service.py` 与 `backend/interface/router/research.py` 的status/report/events/cancel/resume：所有权、前置、最新seq、失败恢复资格、报告未就绪409；PG不可用只诊断error，不能发已完成持久失败的假done。
  - cancel/resume/report已经接同一幂等事务和owner查询，27项HTTP+PG回归通过；events、Runner及真实配置可用性尚待接入，不勾选。证据：[HTTP生命周期](evidence/t019-http-lifecycle.md)。
- [ ] T020 [US2] 新增 `backend/application/tool_calls.py`（新）、更新 Repository/ContentStore 契约与实现：调用身份、预算事务预留、结果hash缓存、uncertain记录、恢复不重置预算；用 `backend/tests/integration/test_mono_tool_cache.py` 覆盖成功缓存不重发、不确定窗口只读重放有记录、并发不超预算。内容 Adapter 必须真实持久化调用缓存，不能用空存根。
  - 工具结果专用 MinIO 内容寻址缓存已完成真实并发/跨客户端读回、缺失/损坏测试；调用预算和 PG 账本尚未接入，不勾选。证据：[内容缓存](evidence/t020-content-cache.md)。
  - 调用身份/记录与单次模型计量接口已补；真实基础设施探针取得供应商用量。PG预算事务及恢复闭环仍未实现，不勾选。证据：[调用身份与计量](evidence/t020-call-contracts.md)。
  - 预算纯策略含终末预留/未结算调用/deadline反例已通过；磁盘断连后改用独立真实测试PG完整回归452通过，原PG数据未修改且仍不能启动。策略不代表事务预算完成。证据：[预算策略与隔离回归](evidence/t020-budget-policy.md)。
  - PG预算 baseline/逐尝试账本已接真实 MinIO 服务；有真实供应商单次调用后缓存复用探针。并发预算、跨新租约成功缓存不重发、uncertain/取消/失租回滚均有真实PG测试；对象落盘到PG成功前的恢复定位、正式phase callback仍待补，不勾选。证据：[调用账本](evidence/t020-ledger.md)。
  - 结果候选定位与已知用量先提交PG，再写/校验MinIO；新租约可结算原尝试。真实独立进程SIGKILL覆盖写入前/后两窗口，已写对象不重发、未写只允许显式只读重放；正式phase callback仍待绑定，不勾选。证据：[恢复窗口](evidence/t020-ledger.md)。
  - 正式plan的LLM callback已绑定预算/cache及共享进程模型并发门；其他工具将随对应worker接入，完整Orchestrator尚未组合。最终全量487通过；T020仍不勾选。证据：[phase绑定](evidence/t017-phase-dispatch.md)。
  - SearchTools已接PhaseTools/RunDriver显式绑定：冻结类别与原定query权限、每provider实际attempt预算/MinIO缓存、当前超时第二次只读重试与既有uncertain拒绝、缓存损坏/预算错误不降级。真实PG/MinIO与受控provider/Driver集成6项通过。Fetch/KB/analysis与正式业务worker组合仍未完成，T020不勾选。证据：[搜索账本绑定](evidence/t025-search-adapters.md#正式run逐来源账本绑定t020t027部分)。
  - FetchTools已接同一Driver/PhaseTools：本query的真实搜索候选授权、冻结parser/Run内容范围、Fetch预算与引用缓存、跨query授权后复用、原文缺失不重下载。HTTP回放→真实HTML Parser→真实MinIO→Source/Evidence门及真实PG账本定向组合24项通过；正式worker/PDF/KB/analysis与CLI仍待完成。证据：[Fetch绑定](evidence/t026-download-safety.md#正式run-fetch绑定t020t026t027部分)。
- [ ] T021 [US2] 更新 `backend/cli/container.py`、`commands/run.py`、`commands/phase.py`、`commands/dump.py`、`phase_state.py`、`output.py`、`__main__.py`：复用start_frozen/execute_phase/最新seq；phase只调一阶段且不写Session/Report；stdout单JSON、退出码、signals/owner/租约符合API §5，日志脱敏；fake seed固定ID/时间/结果且real不宣称确定；更新现有CLI单测。
  - 2026-10-08外围实时诊断：real run --verbose在冻结提交后立即给session/run身份，可在模型等待中启动另一dump读取seq；stderr限定身份/阶段/查询单元计数与Checkpoint，不打印query/prompt/结果正文，stdout单JSON不变。PG真实子进程验证运行中dump和取消，无假完成帧；完整任务仍未完成。证据：[运行中诊断](evidence/t021-cli-cutover.md#real-run实时身份与单元诊断2026-10-08)。
  - 2026-10-08外围调试库选择：real run新增--debug-db，使用已有dr4a_debug且保留显式Parser/连接参数；匿名development之外及fake在连接前拒绝，不创建/迁移用户数据库。受控子进程验证路由与提前拒绝，既有真实隔离PG/MinIO运行回归通过；完整任务仍未完成。证据：[调试库选择](evidence/t021-cli-cutover.md#real-run显式调试库选择2026-10-08)。
  - 2026-10-08外围错误收尾：独立phase正式合并校验拒绝也保留最后已合并本地state/events/usage/failed_unit_id，安全退出1/invalid_state；不放宽不可变事实规则或将worker内部未分类异常误标为契约冲突。受控CLI子进程反例先失败后通过；完整任务仍未完成。证据：[合并拒绝诊断](evidence/t021-cli-cutover.md#独立phase合并拒绝诊断2026-10-08)。
  - 2026-10-07外围快照调试：独立research phase重新Fetch后核验并保留输入中同ID/hash公开原文的只读引用，新写入仍为独立目录；修复仅目录不同造成的不可变Source合并冲突。探针仅额外接受输入快照明确记录的原文，不扩大任意跨目录权限。完整workers/fake迁移与真实Research验收仍未完成，不勾选。证据：[既有原文引用](evidence/t021-cli-cutover.md#独立phase既有原文引用2026-10-07)。
  - 2026-10-07外围装配对齐：real run与开发HTTP复用PublicResearchExecution的plan/research和公开Search/Fetch/Parser/MinIO工具，仍用CLI限定Runner；错误parser在接受Run前明确退出3，后续worker缺失保留Checkpoint。受控子进程/真实PG-MinIO证明10次检索尝试入账、五章Gap与seq=14可dump、修复调用计费及取消隔离仍成立。完整workers/fake/业务验收未完成，不勾选。证据：[公开research装配](evidence/t021-cli-cutover.md#cli公开research装配2026-10-07)。
  - 2026-10-07外围隔离修正：HttpRuntime服务准备可显式不启动HTTP全库维护/执行器，real CLI采用此模式后仅运行已有owner+Run限定Runner；避免全库维护新增组合使CLI处理无关过期Run，也不继承HTTP debug自动执行。独立CLI进程+真实PG验证外部过期Run完整记录未改；完整workers/fake仍待迁移，不勾选。证据：[CLI扫描隔离](evidence/t021-cli-cutover.md#cli与http维护扫描隔离2026-10-07)。
  - 2026-10-07外围单阶段可观测性：phase的--verbose实时输出单元开始/本地合并/已分类失败到stderr，仅可信单元ID/章节/计数，不打印query或prompt；明确local_only且不改变最终events。受控子进程在worker等待中读取开始帧、SIGINT后无完成帧；完整CLI迁移仍未完成，不勾选。证据：[实时phase诊断](evidence/t021-cli-cutover.md#独立phase实时诊断2026-10-07)。
  - 2026-10-07外围输入/中断：argparse错误也输出标准JSON/退出2，不回显用户参数，help保持退出0；独立phase单次SIGINT保留最后合并state、关闭工具、退出1/phase_interrupted，不取消原持久Run。真实受控CLI子进程验证部分研究单元已合并后中断、原输入/RunMetadata不改且无模型调用。完整workers/fake迁移仍未完成，不勾选。证据：[输入与本地中断](evidence/t021-cli-cutover.md#cli输入错误与独立phase中断2026-10-07)。
  - `dump`已切换owner-scoped正式Repository与当前Run.seq读取；真实CLI子进程验证返工最新seq、归属、无快照与旧schema拒绝。统一JSON错误及未知异常脱敏已补。`run/phase`仍旧执行链，signals/fake确定性与完整组合待迁移，T021不勾选。证据：[CLI dump迁移](evidence/t021-cli-cutover.md)。
  - `phase`已移除旧Orchestrator调用，复用严格State/PhaseInput/execute_phase/merge，正式plan worker已通过真实CLI+供应商调用；仅注册plan，其余阶段在worker实施前明确未配置。phase无PG写入，物理debug用量单列；控制错误不被模型重试吞掉。`run`、多单元阶段/其他工具及完整HTTP组合仍待迁移。证据：[phase迁移与真实CLI plan](evidence/t021-cli-cutover.md#后续phase迁移及真实模型验证)。
  - 正式Runner新增精确owner+Run范围：领取和过期/排队扫描均不处理其他Run，PG容量仍全局/每owner统一；关闭仅中断本worker持租工作。旧CLI verbose不再打印私有prompt/response正文，仅操作长度元信息。`run`入口尚未切换，本项继续未完成。证据：[CLI执行范围](evidence/t021-cli-cutover.md#cli-runner执行范围前置)。
  - `run --real`已脱离旧Container/LegacyResearchService：严格十字段/source/owner→start_frozen→限定Runner/正式Driver/真实PG账本和MinIO缓存，signals持租同事务取消，失败JSON带身份/最新seq。真实子进程SDK本机受控模型验证plan→research未配置失败及dump读回；SIGINT持租取消、非持租/过期不能取消、旧schema不迁移均通过。默认fake仍旧链且明确legacy_fake，完整workers和fake确定性仍待实现；T021不勾选。证据：[real run迁移](evidence/t021-cli-cutover.md#real-run正式冻结与执行入口)。
- [x] T022 [US2] 新增 `backend/scripts/verify_run_http.py`：活HTTP启动/确认/订阅/取消/恢复/取状态，支持显式受控依赖模式；配合 `backend/tests/integration/test_mono_process_recovery.py` 用独立进程SIGKILL测试确认提交后未wake、返工阶段中断、成功缓存后中断。输出SQL终态/seq/attempt证据 `evidence/us2.md`。
  - 已新增接受Run的HTTP/SSE探针（支持显式cancel/resume；创建/确认复用verify_clarify_http，不自动接受模型假设），验证帧Schema/身份/单调seq及终态与状态/报告一致。独立进程活TCP完成Driver报告并重启读取，真实探针CLI子进程无额外attempt/工具调用；受控服务器只允许唯一测试PG/bucket，生产不导入。仍缺HTTP取消/恢复及确认未wake/返工SIGKILL的完整组合证据，不勾选。证据：[US2活HTTP验证](evidence/us2.md)。
  - 上条为历史部分进度。本轮补齐探针CLI创建/回答/审阅后明确确认、活HTTP运行中取消/重复取消，以及真正SIGKILL确认提交未wake和返工窗口；扫描后失败不自动付费执行，HTTP显式resume同Run继续，plan调用数仍1。与已有成功缓存SIGKILL反例一起满足T022受控调度验收；默认生产workers、JWT/能力检查及真实业务报告仍属T016/T018/T019与US3–US6，不能由此称整个US2/业务完成。证据：[US2恢复验收](evidence/us2.md#t022完成复核)。

里程碑 M2：T014/T020/T022证明生命周期与恢复；内存/task/SSE丢失不能抹掉PG事实；受控报告不作为A6真实业务验收。

## 5. 阶段 3：US3 计划与真实原文取证

目标：冻结Brief经plan/research产生有定位的证据链；先接公开来源，本地KB桥接在US5完成。

设计：MODEL §4.1–4.2；FLOW §3.2–3.3；API §6–7；OPS §1–2/§6。验收 A4部分、A6引用基础。

- [ ] T023 [US3] 在 `backend/tests/unit/test_plan.py`、`test_scout.py`、`test_scout_trace.py`、`test_scout_observations.py` 写五章覆盖/空计划失败、摘要不成关键证据、arXiv不自动peer_reviewed、Spec无Claim仍有Gap、观察保留表头/单位/脚注等反例。
  - 原文/coverage反例集中在 `test_scout_originals.py`：24项通过，覆盖搜索摘要拒绝、解析hash/版本/定位、防裁切原子表格、arXiv版本身份/非peer_reviewed、无Claim的Spec、条件/来源门与支持反驳冲突。尚缺正式抽取Observation与worker/CLI路径反例，不勾选；证据见[原文边界](evidence/t026-download-safety.md#scout原文与coverage边界t023t027部分)。
- [ ] T024 [US3] 更新 `backend/domain/research/agents/architect.py` 的plan与 `backend/domain/research/state.py` 的SectionPlan/ClaimSpec/AnalysisRequirement Schema：恰section_1..5、至少一主张/查询、分析需求计划态参数、任务维度覆盖，结构失败有界修复后明确失败。
  - 正式`architect.plan`已迁移严格Brief/五章/有界修复，公开evaluation_design真实模型调用暴露并修正“文字协议表误入数值分析”问题，保存完整回归输出/hash；旧CLI显式legacy_plan，未称正式CLI可用。任务维度质量门/三类型代表性与取证尚未全部验收，不勾选。证据：[真实plan worker](evidence/t024-plan-worker.md)。
- [x] T025 [US3] 更新 `backend/infrastructure/search/arxiv.py`、`bocha.py`、`composite.py`：来源并发/限流/timeout、规范SearchResult、真实空结果与依赖错误区分；原有 `backend/tests/contract/test_search.py` 增单源故障和全部失败行为。扩展测试拆至 `test_search_runtime.py`。证据：[搜索契约与真实单源降级](evidence/t025-search-adapters.md)；真实 Bocha 返回9候选，arXiv超时，不能据此认定论文正文或完整research验收通过。正式Run逐请求账本接入仍由T020/T027完成。
- [ ] T026 [US3] 新增 `backend/infrastructure/fetch/http.py` 与真实取证内容存储（建议 `backend/infrastructure/storage/minio.py`）：原文下载/版本/hash/定位/不可变对象、SSRF/DNS/重定向/大小限制；对论文PDF复用Parser、不保存假正文；补 `backend/tests/contract/test_fetch.py`、`test_content.py`，含私网/重定向/同键异hash拒绝。部分证据：[受限下载与HTML内容闭环](evidence/t026-download-safety.md)；下载防SSRF、共享MinIO原文/解析不可变存储、HTML真实行号定位及FetchedDocument已实现，仍缺PDF Parser、Run工具绑定、真实论文闭环。此前Fake-IP阻断属于历史环境故障；2026-10-06重新查询arxiv.org已返回4个公网地址，未放宽SSRF保护，DNS恢复不等于PDF Parser验收完成。
- [ ] T027 [US3] 更新 `backend/domain/research/state.py`、`ids.py` 和 `agents/scout.py`：Source/Evidence/Claim/Link/Observation/Coverage/Gap严格Schema与原文摘录校验、条件稳定ID、章节相关材料、受限追溯/补查、来源可选降级；每单元更新全部相关链路和coverage。接T017的单元提交/进度，不等整phase结束才保存。
  - 已实现 `agents/originals.py` 的原文身份/精确摘录/完整原子块门与 `agents/coverage.py` 的逐Spec确定性覆盖。仍未注册正式research worker：工具授权/账本、抽取Claim/Observation、追溯/补查、逐单元保存与CLI真实取证尚待接入。旧Scout已明确标为legacy，不能当正式worker调用。
  - 已新增正式 `research_worker` 与 `agents/extraction.py`：原文摘录/关联/观察的有界结构化修复、SRO+conditions的Claim ID、Evidence+row/column的Observation ID、逐query/coverage保存；受控模型+真实PG/MinIO/下载器/HTML Parser验证五章10单元、共享Claim合并及空搜索Gap。跨章Spec追加受章节权限约束，既有自然键内容不能覆盖。真实供应商/PDF、受限追溯/补查、KB及CLI注册仍缺，不勾选；证据：[正式research worker](evidence/t026-download-safety.md#正式research-worker与原文抽取t023t027部分)。
- [ ] T028 [US3] 更新 `backend/tests/integration/test_slice_retrieval.py` 并新增 `backend/scripts/verify_research_phase.py`：从CLI真实plan/research输出，逐条抽查可下载原文、位置/hash、Spec覆盖/Gap；用 `evidence/us3.md` 保存至少一个真实论文来源、一项观察（资料确有数值时）与单源故障降级。无原文时验收应失败或明确Gap，不为完成任务伪造观察。
  - `phase research` 与原文审计探针已接正式worker；真实Bocha/模型/下载/HTML Parser/MinIO产出7条Evidence并重新读取原文验证hash/位置/摘录。前两轮Fake-IP和缺bucket失败保留，未弱化安全检查。输入计划手工构造、PDF未接、无数值观察，真实探针按缺失验收退出1；T028不勾选。证据：[US3真实CLI](evidence/us3.md)。
  - 2026-10-07：PDF调试分支已接真实本地MinerU，真实plan产出五章/15子问题并原样进入research。重试保留两个完成单元、真实Source与各源错误码，但下载的是不相关论文、零Evidence/Observation，随后arXiv两次search_unavailable退出3。发现自然语言子问题直接作为arXiv查询的缺陷；不勾选。表格完整单元格边界已拦截系数/指数误读，但不替代行列和比较条件验证。细节见同一US3证据。

里程碑 M3：`phase plan` 与 `phase research` 可复现真实取证，研究卡点有query/section进度；所有关键Evidence有真实原文，不以“搜到了链接”勾完成。

## 6. 阶段 4：US4 分析、写作、审核与报告交付

目标：三种任务产出cases粒度报告，区分运行完成与研究质量；返工和引用闭环。

设计：MODEL §4.3–4.5；FLOW §3.4–3.5；API §2.5/§6；OPS §1–2/§6。验收 A4–A6。

- [ ] T029 [US4] 更新 `backend/tests/unit/test_data_analyst.py`、`test_code_crafter.py`：ComparisonSet不是单Metric、两个未知不相等、跨dataset/协议/指标不排名、除零/缺样本/不合法参数拒绝、无分析需求合法skip；用原文表格fixture而非孤立数值。
- [ ] T030 [US4] 更新 `backend/domain/research/state.py` 与 `agents/data_analyst.py`：ComparableMetric/ComparisonSet/AnalysisSpec/Artifact模型、组级兼容政策、plan态Spec引用解析到指标、条件/单位归一有依据；partial/incompatible只做条件说明、不交给CodeCrafter。
- [ ] T031 [US4] 更新 `backend/domain/research/agents/code_crafter.py`，新增 `backend/domain/research/analysis_templates.py`：实现五种闭集操作/parameters/output Schema、Decimal、模板版本/hash/回链、skipped/failed及Gap；不能执行模型自由Python。
- [ ] T032 [US4] 更新 `backend/infrastructure/sandbox/docker.py` 与 `backend/tests/contract/test_execution.py`：正确挂脚本/输入、禁网/non-root/只读/资源限制、停止超时容器、附件路径/大小/hash；用 `backend/tests/integration/test_mono_sandbox.py` 真容器验证成功、超时、违规网络/路径拒绝，不只mock docker CLI。
  - 2026-10-08：开发可信脚本内核补齐ro挂载、non-root/禁网/资源限制、限定tmpfs输出、附件bytes/hash与真实容器超时/取消清理；真实Docker9项及contract16项通过。不接管Agent，正式AnalysisSpec闭集模板边界仍待T031协调，保持未完成。证据：[真实Docker内核](evidence/t032-sandbox.md)。
- [ ] T033 [US4] 更新 `backend/tests/unit/test_writer.py`、`test_critic.py`、`test_route.py`：覆盖未改章节Binding保留、section_3事实同样审核、审核实际同版文本、空Binding拒绝、critical/major完整路由、返工上限不直接approved。
- [ ] T034 [US4] 更新 `backend/domain/research/agents/writer.py` 与state：Statements/Binding/TaskPayload严格结构、每实质段落/行定位、按章相关上下文、三个task专属模块、版本复制与超长材料有界处理；未登记正文不能绕审。
- [ ] T035 [US4] 更新 `backend/domain/research/agents/critic.py`、`machine.py`、`backend/application/orchestrator.py`：确定性引用校验+实际正文语义审核、旧issue复核、目标派生失效、最大3回流与终末收缩预算、approved/approved_with_risks/needs_more_work，不可安全交付则failed。
- [x] T036 [US4] 新增 `backend/application/report_serializer.py` 并更新发布Repository：固定0–5+References、三任务第3节、真实引用编号/定位、风险/未闭环说明、HTML转义与白名单附件；Report/done Checkpoint/Run/Session同事务发布。新增 `backend/tests/contract/test_mono_report.py` 覆盖空引用说明、无效链接协议、悬空ID、错draft_version与故障回滚。
  - 纯装配与校验在`domain/research/reporting.py`，生产ReportPublisher已接显式Driver；Repository复查确定性内容，不能删风险/伪造书目/追加正文。51项报告contract、含真实PG/MinIO/独立SIGKILL与HTTP的119项目标集、全量607通过。三任务模型/取证仍为受控fixture，T034/T035/T037/T039保持未完成。证据：[确定性报告发布](evidence/t036-report-serializer.md)。
- [x] T037 [US4] 实现 `backend/interface/router/research.py` 附件端点：owner/Artifact/文件白名单校验、MinIO私有读、媒体类型与错误；补 `backend/tests/integration/test_mono_artifacts.py`，跨owner/路径穿越404，内容不可读503，不暴露storage key。
  - 2026-10-08：当前Checkpoint授权/白名单、Run/Artifact命名空间、不可变hash键、10MiB限额与私有HTTP下载已接入Runtime；真实PG/MinIO14项、全量996项通过。只证明附件交付边界，不代替T031模型分析或T039报告质量。证据：[私有附件下载](evidence/t037-artifacts.md)。
- [ ] T038 [US4] 新增 `backend/tests/integration/test_mono_rework.py`，用可控事实/模型复现research/analyze/write三种返工和多问题合并、旧缓存派生失效、最后一次收缩；核对每次seq/draft_version/issue与输出，没有假approved。
- [ ] T039 [US4] 新增 `backend/scripts/verify_reports_http.py`：为三个task各一份公开Brief，经活HTTP确认/执行/取报告，用真实模型、真实搜索/原文/PG/内容存储；与 `docs/cases/` 比组织结构与任务维度，人工抽查事实引用/比较条件；记录 `evidence/us4.md`。如需分析则用真实沙箱，不把fake完整链称all-real。

里程碑 M4：三个报告结构合格、引用抽查可回链、真实执行状态闭环；质量可needs_more_work，但必须清楚说明未解决项，不能以有Markdown代替通过A6。

## 7. 阶段 5：US5 真实知识库闭环

目标：一个PDF从上传到检索正文，再进入研究报告引用；版本发布、取消/重试/删除可恢复。

设计：ARCH §3–5；MODEL §5–7；FLOW §5–7；API §4/§7；OPS §4–6。验收 A7/A8/A11隐私部分。

- [ ] T040 [US5] 新增 `backend/tests/integration/test_mono_kb_lifecycle.py`、`test_mono_ingestion.py`：先测creating/active/deleting、staging不可见、替换失败旧active保留、重复内容同身份、Document单活动Job、cancel/retry边界、删除后禁止检索。
  - 2026-10-08：先完成真实PG归属、staging不可见、并发内容收敛、Document单活动Job、替换/失败原子性与租约/取消/删除屏障测试；完整Service/Worker、cancel/retry和外部删除仍待T042–T049，不勾选。
- [ ] T041 [US5] 扩展state或新增 `backend/application/knowledge_models.py`：KB/Document/Version/Job/Attempt/Chunk/Progress/RetrievalResult完整Schema；新增 `backend/infrastructure/storage/migrations/0005_mono_knowledge.sql` 和PG Repository，实现归属/内容去重/活动版本约束/清理租约/原子activate+Job完成；真实PG测试父子归属/竞争。依赖T005迁移体系。0003/0004已用于工具调用迁移，使用下一追加序号，不重编号已应用迁移。
  - 2026-10-08：完整实体/检索typed记录、五表/复合FK/内容与活动身份约束、owner查询、submit/claim/activate/fail及同事务发布已补；新的KB内容使用owner/KB/version/hash独立命名空间。模型/真实PG/MinIO目标集62项、最终全量1043项通过；清理租约、完整Job控制/恢复与应用组合仍未完成，保持未勾选。证据：[知识库事务底座](evidence/t040-t041-knowledge-base.md)。
- [ ] T042 [US5] 将 `backend/application/knowledge_base_service.py` 拆分为Management/Ingestion/Retrieval三个Service（建议同目录 `knowledge_base_management.py`、`document_ingestion.py`、`knowledge_retrieval.py`），更新组合根与ports；不维护新旧两个可写事实源，移除生产内存Document/Job状态。
  - 2026-10-08：DocumentIngestionService的持久Job查询/真实源验证重试已接Runtime及HTTP，新增typed Job上下文/Port与私有ContentStore生命周期；公开JobView不含lease/key。Management/Retrieval、上传/清理Worker与旧CLI链路替换尚未完成，保持未勾选。证据：[Job应用层与HTTP](evidence/t040-t041-knowledge-base.md#2026-10-08job-应用层与-http-首批)。
- [ ] T043 [US5] 完成 `backend/infrastructure/parser/pdf.py` 与T026内容Adapter：真实MinerU结构化text/table/formula、页码/标题/脚注、完整原子块、流式50MiB/500页/10000chunks限制；补 `backend/tests/contract/test_parser.py`、真实公开PDF解析测试，空内容失败不完成。
  - 已核对MinerU 4.0.10实际wheel的Content List V1 renderer，并新增纯输出归一化边界与11项契约测试：原始page_idx转1-based页码、整表/公式与标题/脚注保留、空内容/图片-only原子块/非法或乱序页码/超限拒绝。Parser可选依赖已固定；本地权重准备、隔离实际推理、正式PDF Adapter及真实论文验收仍待完成，不勾选。纯输出测试不代表MinerU真实运行。
  - 两组公开权重已按固定commit显式下载并生成文件hash清单（未调用远程解析）；新增隔离子进程PDF Adapter，流式读取/校验原文、限制页数、剥离调用者凭据/配置、本地-only模型与超时/关闭终止。真实PDFium坏PDF/页数前置及真实子进程错误回收3项通过；公开论文真实推理与完整结构/取消/资源验收尚未通过，保持未完成。
  - 真实已存论文PDF本地解析已通过：15页、132文本/5公式/4表，macOS系统禁止解析子进程network；空白文本与实际辅助类型适配修正。原页对照发现Table 2 FLOPs合并单元格拆列，不能当全部数值可信；Linux隔离、完整故障验证及Research回链接入仍缺。证据：[真实MinerU部分验收](evidence/t026-download-safety.md#2026-10-07真实本地-mineru-pdf-解析t043-部分)。
- [ ] T044 [US5] 新增 `backend/application/document_ingestor.py`：结构切片、正文/anchor区别、manifest与稳定chunk ID、每批进度、幂等写入、取消检查、hash校验；依赖T041–T043，先保持staging，不能自行发布active。
- [ ] T045 [US5] 更新 `backend/infrastructure/embedding/bge_m3.py`、`bge_reranker.py`、`backend/pyproject.toml`、`uv.lock`：锁真实FlagEmbedding模型/tokenizer revision与本地权重、有界推理/批量；dense1024+sparse非空真实样本、有限值与rerank形状契约测试，禁止通用encode冒充sparse。
- [ ] T046 [US5] 更新 `backend/infrastructure/vector/milvus.py`：固定collection/per-KB partition、稳定主键、dense COSINE/sparse IP、双ANN+RRF60、授权版本过滤、strong可读校验/删除；用 `backend/tests/integration/test_mono_milvus.py` 真Standalone验证写读同映射、staging过滤及版本替换，不使用Lite。
- [ ] T047 [US5] 完成IngestionService/TaskRunner：上传对象先落再accepted、claim/heartbeat/批次manifest、索引可读后PG原子activate、新active旧retired、同Job/version重试、attempt上限/历史、cancelling清理；实现 `backend/interface/router/knowledge_base.py` 与入库Job路由（新 `ingestion_jobs.py`）。依赖T044–T046，补T040真实故障点测试。
  - 2026-10-08：PG内核已补heartbeat/单调进度、取消清理租约与token接管、显式源/清理验证门、同Job/Version重试与尝试上限、过期processing失败记录/删除屏障转清理；本批14项真实PG测试、邻接目标61项与全量1057项通过。尚未组合Service/TaskRunner/HTTP及真实MinIO/Milvus清理，不勾选；证据：[Job控制与恢复](evidence/t040-t041-knowledge-base.md#2026-10-08job-控制与过期恢复的-pg-内核)。
- [ ] T048 [US5] 完成KnowledgeRetrievalService与 `backend/infrastructure/retrieval/local.py`：PG可见版本/授权→双路召回→PG验证→MinIO正文→重排→删除屏障再检；typed filter/top_k/trace与显式rerank降级，依赖错误503不返回空。扩展 `backend/tests/integration/test_slice_kb_search.py` 的真实回链与错误测试。
- [ ] T049 [US5] 完成ManagementService/router：分页/CRUD/revision、数据分类不可放宽、KB/Document删除屏障与清理cursor、creating/deleting同租约、停止旧任务/清迟到外部写入、保留墓碑；用T040检查重启继续清理、completed历史版本退役仍合法。
  - 2026-10-08：新增严格属性Patch与真实PG CAS更新、KB/Document删除屏障及清理起点，重复删除不重复递增revision，保持归属/父子锁顺序；删除后正文不可见、迟到发布拒绝、元数据/Job历史保留。17项新增真实PG/Schema测试、邻接目标89项通过。HTTP管理/分页、完整清理租约与外部删除恢复仍缺，不勾选；证据：[管理事务与删除屏障](evidence/t040-t041-knowledge-base.md#2026-10-08管理事务与删除屏障)。
  - 2026-10-08续批：creating/deleting共用KB租约，Document清理独立租约；领取/续租/释放、fencing token、revision+顺序cursor提交、SQL时钟拒绝过期及可信扫描已补。真实SIGKILL证明已提交cursor保留且新Worker接管后旧token失效；新增14项与邻接103项通过。Service/TaskRunner、真实MinIO/Milvus清理及deleted墓碑迟到写入核查未完成，仍不勾选。证据：[清理租约与进程中断](evidence/t040-t041-knowledge-base.md#2026-10-08清理租约与进程中断)。
- [ ] T050 [US5] 在 `backend/application/retrieval_bridge.py`（新）实现Research只读Port到KB Service桥，确认事务冻结VersionReference；更新scout将DocumentVersion登记Source再建Evidence。验证新版本不能偷偷替换冻结来源、删除写Gap、private-only研究不向外LLM/搜索发送资料。
- [ ] T051 [US5] 更新CLI `commands/ingest.py`、`search.py`，新增 `commands/kb.py`、`job.py`，更新 `__main__.py`：UUID资源/真实与fake模式、异步接受与--wait、幂等key、Job重试/取消、结果trace；扩展 `backend/tests/unit/test_cli_kb.py` 与subprocess测试，禁止default名字隐式访问。
- [ ] T052 [US5] 新增 `backend/scripts/verify_kb_roundtrip.py`：用公开PDF跑CLI+HTTP真实上传/Job轮询/检索/研究引用，核对PG IDs/对象hash/向量查读与报告位置；注入替换失败、取消、重复上传、完整删除并只清本轮测试资源；保存 `evidence/us5.md`。声明local模型、真实MinIO/Milvus/Parser，不用fakeParser替代。

里程碑 M5：A7/A8真实证据齐；仅检索分数不算正文闭环，仅删除数据库登记不算完整删除。私有KB外部调用计数必须为0。

## 8. 阶段 6：US6 正式身份、安全与部署恢复

目标：把已跑通闭环按目标环境可部署、可恢复、可保护数据；开发匿名与正式认证共用业务政策。

设计：API §1/§3；OPS §4–7。验收 A9–A11/A13。

- [ ] T053 [US6] 更新 `backend/application/auth_service.py`、`interface/router/auth.py`、`dto/auth.py`、`deps.py`、UserRepository与依赖锁：PG用户、Argon2id、JWT固定算法/iss/aud/sub/exp、Bearer/cookie冲突、login cookie/logout语义/CSRF/CORS；扩展 `backend/tests/integration/test_auth_guard.py` 和 `test_mono_auth.py`，含128 Unicode密码与开发用户不可登录。
- [ ] T054 [US6] 在 `backend/application/settings.py`、Research/KB服务、Fetch/LLM Adapter落实隐私边界/速率限流/日志脱敏/prompt隔离；新增 `backend/tests/integration/test_mono_privacy.py`、`test_mono_security.py`：private摘录及派生query不外发、无本地LLM提前409、SSRF/DNS重绑定防护、跨owner检索/附件拒绝、生产匿名配置启动失败。
- [ ] T055 [US6] 扩展 `backend/application/task_runner.py` 与PG/清理Service：SIGKILL后扫描、Run过期failed显式resume、Job有界自动恢复、creating/deleting恢复、孤儿TTL/引用校验；扩大 `backend/tests/integration/test_mono_process_recovery.py` 到运行/入库/清理各断点及heartbeat失租，不把graceful测试代替崩溃测试。
  - 2026-10-08：新增独立Python Worker真实SIGKILL测试，证明清理lease/cursor提交后中断、租约过期扫描与递增token接管，旧token拒绝；仅PG清理内核边界，尚非TaskRunner或外部清理恢复验收，保持未勾选。证据：[清理租约与进程中断](evidence/t040-t041-knowledge-base.md#2026-10-08清理租约与进程中断)。
- [ ] T056 [US6] 更新 `backend/interface/main.py`、CLI doctor、结构化日志与指标：liveness/readiness区分、真实schema/内容/索引/模型/Parser/执行器能力、可选源degraded/必需功能503、queue/phase/query/预算/取消延迟；补 `backend/tests/integration/test_mono_readiness.py`，诊断不得收费调用或泄露凭据。
  - 2026-10-07最低调试诊断：doctor改用Settings，新增scope=research与debug-db；只读核对当前迁移集合/Run表、认证访问MinIO bucket，拒绝空配置/未准备Hub模型/空权重目录，明确未验证能力且不收费/写入。实测独立debug库通过、恢复旧库仅Schema失败；真实隔离PG/MinIO及CLI定向21项通过。完整HTTP readiness/模型Parser执行/索引/指标仍暂缓，不勾选。证据：[doctor边界](evidence/t002-settings.md#2026-10-07最低研究调试诊断t056部分)。
- [ ] T057 [US6] 更新 `backend/Dockerfile`、`docker-compose.prod.yml`、`backend/.env.example` 与执行Adapter：生产无Docker socket、提供有相同安全限制的隔离Worker、模型两套固定权重挂载/依赖锁/单ASGI worker/迁移；新增 `backend/scripts/verify_deployment.py`，从清洁部署验证readiness及真实沙箱，不以Compose注释当实现。
- [ ] T058 [US6] 新增 `backend/scripts/verify_backup_restore.py`：以测试资源验证PG+MinIO一致边界备份、hash/引用检查、Milvus从manifest重建、恢复后研究读取；记录步骤与实测到 `evidence/us6.md`，不得试验覆盖现有用户库。

里程碑 M6：正式鉴权、安全/隐私与部署恢复测试通过；环境缺能力则报告阻塞，不隐式退回fake/exec()。

## 9. 阶段 7：完整回归与客户端交接

设计：API §1–7；OPS §8 A1–A13。

- [ ] T059 [交接] 更新 `backend/cli/README.md`、现有 `backend/scripts/smoke_e2e.py`/`smoke_real.py` 的用途/命令：移除旧端口、隐式freeze、最高phase恢复和“全real”误称；实现指向mono及新验证入口的准确说明，不再复制API规范。
  - 2026-10-07外围调试交接：两个旧smoke入口已改为无依赖、无I/O的退役提示，退出2并指向现有HTTP/CLI验证入口；CLI README纠正real run完整报告及历史契约权威误称。模块/文件两种子进程启动共4项反例先失败后通过，连同debug profile共6项通过。完整CLI迁移/交接仍未结束，本任务不勾选。证据：[调试入口清理](evidence/t021-cli-cutover.md#旧smoke入口退役2026-10-07)。
- [x] T060 [交接] 更新 `tui/src/api-client.ts`、`app.ts`、`tui/test/api-client.test.ts`：当前brief_version/Idempotency-Key、SourceSelection、SessionView、失败done/统一error、重连只读状态；保留无登录开发用法、401明确解释。用活后端手工/脚本验证多轮、退回、确认、进度、取消、报告，不以客户端mock测试代替HTTP集成。完成复核：[TUI客户端交接](evidence/t018-sse-core.md#t060客户端契约交接完成复核2026-10-07)。
  - 2026-10-08状态竞态补充：手动refresh与observer均不接受低版本/低seq的迟到读取；不同Session/Run身份明确拒绝，会话切换后旧refresh不能用于发送resume。done后仍GET确立终态，不凭客户端自行终结。证据：[迟到状态读取](evidence/t018-sse-core.md#tui迟到状态读取2026-10-08)。
  - 2026-10-08故障窗口补充：响应头已到但正文传输中断不再误判JSON契约错误，保留原变更请求供/retry沿用幂等键；完整非法JSON仍拒绝。实际TCP中断反例先失败后通过，未增加自动重试。证据：[正文中断](evidence/t018-sse-core.md#tui响应正文中断重试2026-10-08)。
  - 完成客户端自身验收，不继承T039真实报告或T053正式认证完成状态。实际main.ts与pi-tui在POSIX PTY接独立TCP后端、真实PG/MinIO、受控模型，键盘完成Clarify/明确确认/取消/CLI提示及实时query进度/受控报告渲染；controller活HTTP验证多轮/退回/重开/显式resume。此前终端缺口已补，下面是历史进度。
  - 2026-10-07外围客户端验证：实际TypeScript控制器通过独立TCP后端/真实PG/MinIO验证运行中取消→done.cancelled/无报告，以及SIGKILL→维护扫描failed→TUI显式resume同Run/seq→受控报告和query进度；已提交plan调用未重复。多轮/退回/明确确认/CLI dump/ready取消由同文件覆盖。模型与报告为受控fixture，终端交互呈现及完整业务交接仍未全部验收，不勾选。证据：[TUI运行控制](evidence/t018-sse-core.md#tui活http运行控制2026-10-07)。
- [ ] T061 [交接] 新增 `backend/scripts/verify_mono_suite.py`，调度前述HTTP/phase/KB/deployment验证脚本，逐项声明依赖与结果；缺资源/网络/模型不标通过。跑backend全量pytest、TUI test/typecheck、Web测试/typecheck/build及真实代表性E2E，保存 `evidence/final.md` 对应A1–A13与T066 Web验收，列出未通过项与不能承诺exactly-once的窗口。依赖T063–T066。
- [ ] T062 [交接] 审核实际代码与五份mono的Schema/枚举/状态码/事务/阶段/隐私/版本一致性，核对三报告引用及失败路径证据；必要设计修订同时修改受影响mono而非局部客户端设计。确认所有完成任务有证据链接，工作区未包含密钥/私有正文/不相关用户文件；只有全部验收通过才宣布后端mono-v1完成。

## 10. 依赖、执行入口与完成定义

### 追加：Web 研究聊天客户端

沿用五份 mono 文档中的 HTTP/SSE，不新增客户端专属业务规则。实现落点为根目录 `frontend/`；参考 ChatGPT 的布局与交互，不复制品牌资源。本轮交付研究聊天客户端，不额外扩张为完整 KB 管理后台。

- [ ] T063 [Web] 建立 `frontend/` 的 TypeScript Web 工程、依赖锁与开发/构建/测试命令，提供配置明确的后端地址或同源代理；实现响应式侧栏、聊天记录、底部输入框与空状态，键盘可操作、窄屏可用。在 `frontend/README.md` 记录启动方式与配置；不把密钥或后端 `.env` 打进浏览器包。通过 typecheck/build 与基础组件测试。
- [ ] T064 [Web] 实现 typed HTTP 客户端与研究交互：创建201 ask/confirm、多轮回答、展示十字段 Brief、显式确认或退回修改、brief_version与幂等 key、GET恢复会话。开发匿名和正式 cookie 登录使用同一契约，401给出明确操作；不自动确认 Brief、不在浏览器维护第二套权威状态。测试初始确认、缺字段、版本冲突、重复请求与网络失败。
- [ ] T065 [Web] 实现 SSE 事件级展示：bootstrap/phase/progress/rework/error/done更新同一对话中的运行卡片，区分运行状态与研究质量；断线后只读状态并重新订阅，不重新启动研究。支持取消、契约允许时显式resume、报告读取与安全 Markdown/附件展示；离开会话关闭旧订阅。测试失败/取消done、迟到订阅、重复事件、重连、脚本注入与未知事件，不伪装逐 token 输出。
- [ ] T066 [Web] 使用真实浏览器与活 HTTP 后端验证完整研究聊天流程：新建、多轮、退回、确认、进度、刷新恢复、取消/恢复、报告；检查宽屏/窄屏布局、键盘、加载/错误反馈与安全渲染。复用已通过的真实业务报告，并记录模型/依赖模式，不能把 mock 页面称真实 E2E。保存 `evidence/web.md`，所有测试/typecheck/build通过后才勾选。

Web执行顺序：T063 → T064 → T065 → T066 → T061–T062；T064依赖T012/T053，T065依赖T018–T019/T036–T037。可以提前做布局与客户端确定性测试，但不能提前宣称真实业务闭环。

推荐主线：底座 T001–T007 → US1 T008–T013 → US2 T014–T022 → US3 T023–T028 → US4 T029–T039 → US5 T040–T052 → US6 T053–T058 → 交接 T059–T062。

以下实际交叉依赖不可遗漏：

- US3的PDF正文取证需要T043真实Parser能力；可提前实施T043与其必要依赖，不等整个US5完成。在此之前HTTP/HTML原文取证可验证，但PDF不能用存根通过。
- US4真实Artifact需要T032；生产隔离验收另需T057。无分析要求可skip，不代表沙箱任务完成。
- US1先用公开来源；KB不存在/不归属必须拒绝，不能为尚未实施KB而接受无效ID。私有KB的正式闭环依赖T050/T054及本地LLM。
- US5源文件/派生正文存储复用T026真实ContentStore，任务调度复用T016；不能再建第二套对象事实或内存Job队列。
- US2的真实Run/阶段通过受控输出可先验证；真实业务通过必须补T028/T039/T052，不把M2当M4/M5。

每个任务勾选前必须有：实现路径与设计来源、对应正反例测试结果、必要的真实依赖证据、未引入另一套契约。涉及外部模型的验收记录实际模型版本与费用边界；网络不可达的失败保存为未完成，不伪造成功。

验收覆盖索引：A1=T008–T013；A2=T006/T008/T011/T053；A3=T006/T015/T036；A4=T027/T033–T038；A5=T029–T032；A6=T034–T039；A7=T043–T048/T050/T052；A8=T040–T041/T047/T049/T052；A9=T020/T022/T055；A10=T014/T018–T019/T022；A11=T007/T050/T053–T054；A12=T021/T051/T059；A13=T056–T058/T061。

**当前下一步：真实业务worker与工具（T023–T035，含T032真实沙箱与必要的T043 Parser） → 默认T016 Runner组合 → T021 CLI → T022完整HTTP与独立进程返工恢复验收。** T015租约/取消/恢复/报告事务和T019 HTTP生命周期已接入；T018默认HTTP可订阅当前持久状态。最新隔离真实PG/MinIO环境全量607通过，工具结果写入前/后及单元提交到阶段推进之间的真实SIGKILL恢复已验证；原PG在磁盘断连后不能启动且数据未修改。正式phase executor/plan worker、PG预算/cache与共享模型信号量、单元manifest/完整seq提交/可验证跳过、稳定单元规划与typed Machine/独立阶段提交、五阶段/返工RunDriver及显式Runner已组合（证据：[Driver与恢复](evidence/t017-phase-dispatch.md)）；T036确定性报告装配/发布校验已完成（证据：[报告serializer](evidence/t036-report-serializer.md)），默认ready Run尚不自动研究，真实业务worker、预算提前耗尽的最终收缩仍待实施；不能宣称M2或业务报告完成。T011仅KB授权/版本/隐私部分等待T041/T050，当前对KB请求明确404且零模型调用。历史阶段说明记录当时边界，以最新证据为准。Web仍按T063–T066纳入最终交付。
