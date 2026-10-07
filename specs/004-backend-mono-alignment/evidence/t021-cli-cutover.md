# T021部分进展：正式Checkpoint读取

日期：2026-10-06。设计依据：mono API §5、MODEL §3、FLOW §4。T021保持未完成，run/phase仍待切换；不会把旧CLI fake报告称为mono验收。

## 实现

`application/research_queries.py::checkpoint_view` 在同一事务锁住owner Session，读取它的当前Run及精确checkpoint_seq，校验phase、归属、冻结Brief和config一致。`cli/commands/dump.py` 使用共享Settings/正式PostgresResearchStore；移除旧PostgresStateStore和最高phase倒序查找。

开发默认固定owner；`--owner UUID`指定已有用户，production必须显式指定。dump不创建用户、不执行迁移、不领取Run、不启动模型、不改业务事实。有会话而无Run为checkpoint_not_found，其他owner会话与不存在会话均session_not_found。成功state为完整mono PipelineState，并返回checkpoint_seq。只读命令不能伪装将旧schema自动升级；旧schema返回退出码3/schema_incompatible且不重试。

`output.py`成功JSON有error:null；入口UsageError/AppError/AdapterError/未知异常均形成标准Error对象、单个stdout JSON。stderr诊断仅输出status/code；未知外部异常正文不泄露。此批不涵盖argparse自身的所有参数错误JSON化、旧worker的verbose正文日志或signals，因此完整CLI输出安全仍待后续T021收尾。

## 实测

`test_mono_cli_dump.py`使用真正CLI子进程、当前解释器及每例唯一真实PG数据库：

- 提交seq=2 research、seq=3 analyze、seq=4 research；dump返回seq4而不是较晚阶段的seq3，完整state一致。
- Dump前后Session.revision/status、Checkpoint数、Report数不变。
- 未冻结会话与不存在会话错误不同；既有其他owner无法读取，未知owner明确失败。
- 旧0001 schema返回schema_incompatible，迁移记录仍一条，无research_runs表。

统一错误测试验证秘密样例SDK异常不出现在stdout/stderr。最终定向 `test_mono_cli_dump.py test_cli_dump.py test_cli_output.py`：**10 passed in 4.36s**。补充旧schema前置之前全量 **761 passed in 84.02s**；补充后定向验证通过，未把该全量结果误称最新版本全部762项已重跑。Ruff/format/diff检查通过。

首次全量发现MinIO未运行，8个fixture连接拒绝；主动中断该轮（310passed/8errors），单独启动现有deep-research-minio-1，通过其ready探针，再完整回归。没有启动旧Compose PostgreSQL或修改恢复库。

当前恢复业务库直接运行dump返回schema_incompatible：它恢复的是旧业务schema，数据库连接恢复不等于mono迁移完成。没有修改其8个Session、3个Report等历史数据。后续正式写入口需执行已有保全迁移流程并验证历史备份，或继续使用独立开发数据库。

用户文件docs/implementation/未动，未修改.env或代理。此批不解决Compose接管恢复PG卷。没有push。

## 后续：phase迁移及真实模型验证

`cli/commands/phase.py`移除旧Orchestrator与旧Container依赖；输入验证改为完整mono PipelineState和正式PhaseInput，正式PhaseExecutor只给worker授权切片，merge_phase_result返回完整post-state，不推进Machine或阶段。不写Session/Report/Checkpoint、不连接PG；也不因输入中包含真实run_id而借用其租约。当前正式worker只注册已有plan，其余有效阶段明确退出3/service_not_ready，不回退旧agent或输出假成功。

`cli/phase_tools.py`提供隔离debug的模型callback：fake受控计划经过同一plan worker；real校验模型与prompt版本、拒绝未配置的KB授权/私有外发、一次SDK请求且计量实际返回用量。调用次数/token allowance有上限，总deadline与单次timeout受配置约束；debug_usage独立输出，原Run budget_used保持不变。fake不创建SDK或调用外部服务，同一输入和seed成功JSON完全一致。其余工具与持久Run预算由正式Run入口负责，不能用这份无Run输出作为账本验收。

迁移还发现并修正 `_structured` 将AppError当外部异常重试：域定义ExecutionControlError，AppError继承，structured调用立即传播。预算耗尽/旧执行权限/停止控制反例验证同一异常且仅调用一次，没有隐式补调用或改写成dependency_unavailable。

真实子进程测试包含：非法旧review输入不能approved、完整state重新解析、只改变section_plans、不推进phase、不改输入文件、故意无效DATABASE_URL仍成功（无PG路径）、固定seed重放一致；未配置research明确失败；真正Anthropic SDK连接受控本机HTTP并验证请求/用量；私有real在SDK构造之前拒绝。受控HTTP案例不是供应商真实模型证据。

真实供应商验证通过以下**CLI子进程**入口（冻结Brief来自既有公开evaluation_design记录，API key只由Settings读取）：

```bash
uv run python -m scripts.verify_cli_plan --brief ../specs/004-backend-mono-alignment/evidence/t024-plan-real.json --real --record ../specs/004-backend-mono-alignment/evidence/t021-cli-plan-real.json
```

输出：exit_code=0/status=ok，5章；debug_usage={llm_calls:1,input_tokens:225,output_tokens:11068}，model alias deepseek-flash。输入、完整输出与post-state保存 [t021-cli-plan-real.json](t021-cli-plan-real.json)，不含密钥或完整prompt；schema/Brief不变/五章/只改变plans校验通过。记录是这次供应商返回计量，不承诺确定模型输出或账单金额；parser_version等无关能力仍unconfigured，不是整条流水线已就绪的证明。

定向含正式PG工具绑定 **40 passed in 6.44s**；全量 **768 passed in 88.67s**。随后补充私有SDK前置测试与verifier线程写文件，最新plan/CLI定向 **24 passed in 3.20s**；fake verifier也成功，实际外部调用计数0。Ruff/format/diff检查通过。

T021仍未完成：run需切到start_frozen/Runner，research/analyze/write/review及多单元执行待注册，signals/完整输出与T022活HTTP验收未收尾。不能宣称US2或真实research完成。恢复业务库与原损坏卷未改动，没有运行Compose PostgreSQL。

## CLI Runner执行范围前置

2026-10-06，基线`f5a3fb7`。上述T022未收尾是当时状态，现已由us2.md完成受控调度验收；本批是T021入口迁移前的执行权限准备，尚未迁移`commands/run.py`。

TaskRunner可显式指定owner+run_id（必须同时提供UUID）；领取和恢复扫描均带同一范围，不能作为全库后台Runner。HTTP默认Runner不带范围，保持全库扫描行为。ResearchRepository的scan_interrupted增加可选范围，PG与Fake同步；PG仍先锁Session后锁Run、仍按全局/owner容量领取，scope不豁免配额。只给Run不校验owner被拒绝。

真实PG反例：同owner其他Run已持租但过期，CLI范围只领取目标ready、不扫描或终止其他Run；CLI关闭后只把自己的Run记interrupted，外部Run仍保持原状态/token。其他进程持有同owner活租约时，CLI不会绕过容量领取。缺owner或缺Run的范围立即拒绝；既有PG竞争/取消/恢复与Fake契约继续覆盖。

同时修复旧CLI的verbose正文外泄：只打印开始/完成及字符数，不打印prompt/模型响应。秘密样例单测核对stdout为空、stderr仅元信息。

首轮Scope与Repository定向49通过，加入verbose后50通过。`run`仍需正式start_frozen、执行器/缓存组合、信号取消、单JSON含Run身份和fake确定性；不能因本范围前置通过而勾选T021。

最终联合回归（Fake ports、TaskRunner、Run生命周期、HTTP runtime、独立进程恢复和verbose）：**60 passed in 41.21s**。ruff/format与git diff --check通过；本批按影响面定向验证，未重跑或冒称新的全量结果。

## Real run正式冻结与执行入口

2026-10-06，基线`fb606be`。`commands/run.py`的real分支已移除旧Container/LegacyResearchService/Orchestrator，新增`cli/run_real.py`组合正式ResearchService.start_frozen、owner+Run限定TaskRunner、RunDriver、PhaseExecutor、PG工具账本和真实MinIO结果缓存。沿用同一Settings、十字段字符串Brief和三种task枚举；来源/KB/owner约束交给同一App政策。production须显式owner，未知显式owner在开发身份创建之前拒绝。

数据库必须已显式迁移0002；CLI不迁移历史库、不新建bucket。默认开发owner可由正式Runtime创建；无本地模型能力/无API key提前拒绝。只注册已实现的plan worker，其他阶段明确service_not_ready并持久failed，保留安全seq；**尚不能产出完整真实报告**。失败JSON带session_id/run_id/phase/checkpoint_seq，含合法终态events；接受后观察/依赖错误也保留已知Run身份，但不假称PG已有终态。quiet省略events，real忽略seed。

SIGINT/SIGTERM设置停止请求；在同一PG事务核对worker/owner/run/未过期租约后请求取消，再关闭Runner。其他worker已持租、已失租不被取消；发布/取消竞争以再次读取的PG终态为准。关闭先Runner、再内容客户端、模型和pool，恢复原signal handler。成功路径只有PG报告/终态一致才输出FinalReport JSON，但当前正式workers未齐，此批没有实测完整成功报告。

模型预算增加可选真实output_token_limit：每prompt用UTF-8字节+64+输出上限作为保守预留，不能预留全部剩余Run预算，否则第一次失败耗费后无法有界修复。超出上限在SDK/账本调用之前拒绝；按供应商返回用量结算。明确输出上限进入调用版本hash，未指定的新字段保持旧hash兼容。该预留不是实际token统计或账单金额。

### 真实CLI子进程证据（供应商受控，不是all-real业务）

- 从完整冻结Brief直接创建Session/Run，真正Anthropic SDK只发plan请求，无Clarify；真实PG/MinIO保存plan后最新seq=3/phase=research。缺research worker返回exit3/service_not_ready，PG failed，reports=0。
- 单次受控模型返回input20/output30，tool_call_attempts=1，Checkpoint预算tokens=50；`cli dump`子进程读回完全相同的state/Run/seq。
- 首次模型输出not JSON再修复：物理SDK请求2、账本2、已计tokens=100；修复后的plan保存，不因第一次消费而错误耗尽预算。
- SIGINT与SIGTERM分别在真正SDK HTTP等待期间发送，只取消本CLI持租Run；退出1/cancelled、reports=0，quiet无events。
- 其他worker或过期租约取消函数返回false，原Run仍running。未迁移的空schema返回3/schema_incompatible，public表数仍0且模型请求0；列表版旧Brief在适配器前被拒绝。
- 默认fake尚用旧内存链，输出明确legacy_fake；fake确定性/完整正式workers及更细事件仍待实施，T021不勾选。

首轮2条主路径最终 **2 passed in 4.50s**。首次测试Model server在关闭时等待挂起连接产生清理死锁，已中断该轮（1 passed/91.45s）、验证独有PG/bucket清理，再修正先取消handler后等待server关闭；没有借此重启真实用户服务。

迁移+Runner/旧CLI定向 **27 passed in 13.23s**；CLI run/dump/phase **21 passed in 12.00s**。预算/Driver定向 **40 passed in 21.75s**，修复分支/预算与CLI **29 passed in 10.94s**；补信号和HTTP投影竞态后，含独立SIGKILL的联合 **40 passed in 41.11s**。

中间全量 **798 passed in 135.25s**；随后全量暴露1个HTTP测试竞态（798 passed/1 failed）：独立SQL观察到completed不代表finished回调已经执行。测试改为等所持Runner task结束再断言回调，不延迟或修改PG发布事实。此点体现投影在持久事实之后，而不是业务失败被忽略。

最终完整版本全量 **801 passed in 138.26s**；ruff/format及git diff --check通过。未修改用户历史库、恢复卷、.env或docs/implementation；本批仅唯一测试PG/bucket，未向真实供应商收费。
## 旧smoke入口退役（2026-10-07）

范围：T059部分，开发者调试交接；不涉及Agent prompt/tool、Serializer或共享Schema变更。

发现：`scripts/smoke_e2e.py`直接调用旧Service，隐式同意默认假设并声称完整real E2E；`scripts/smoke_real.py`默认收费调用模型并向硬编码5433的旧库写固定`smoke-1`记录。两者不符合mono HTTP验收与隔离测试约定。

修改：保留两个可执行入口，但只打印退役原因和`cli doctor`、`verify_clarify_http`、`verify_run_http`替代命令到stderr，退出2；不加载环境文件或Adapter，不发请求、不写数据库。没有自动重定向到任何可能付费/写入的替代命令。CLI README明确当前real run只注册plan、不保证完整报告，并转向mono权威契约。

验证：`uv run --no-sync pytest -q tests/unit/test_retired_smoke.py tests/unit/test_debug_backend.py`：6 passed。新增4项分别以module/file启动两个旧入口，并通过`python -S`移除site-packages；先验证旧实现4项失败，再验证新实现全部通过。检查退出2、stdout为空、stderr有替代命令且无异常堆栈/凭据canary。此验证是本地入口回归，不是研究E2E，不包含收费模型或真实存储写入。

扩大回归：`uv run --no-sync pytest -q tests/unit/test_retired_smoke.py tests/unit/test_debug_backend.py tests/unit/test_cli_doctor.py tests/unit/test_cli_output.py tests/unit/test_verify_run_http.py tests/integration/test_mono_cli_phase.py`：34 passed in 6.34s；涉及变动Python文件的Ruff与`git diff --check`通过。没有运行全量或真实业务E2E，不以该目标集宣称它们通过。

边界：T059仍未完成；T021完整workers/fake迁移与T056完整能力诊断仍未完成。本次不修改任务全量完成定义。

## CLI输入错误与独立phase中断（2026-10-07）

范围：T021的输入/输出及局部运行控制，不更改Agent或共享Schema。原argparse错误在JSON模式退出前只写stderr文本；新的入口捕获safe UsageError，统一返回单个stdout JSON、标准Error、退出2。原参数值不回显，避免未知命令/选项/非法枚举带入私密query/路径。help仍退出0，正常帮助不包JSON；`--`后的字面`--json`不认作开关。缺必需参数、未知phase、未知scope/选项及缺命令5项反例先失败后通过，真正CLI子进程验证退出2/单JSON/无stack或canary泄漏。

独立phase收到单次SIGINT时，在协程取消后关闭本地工具、输出最后已合并state/delta/events/本次usage及失败unit_id，退出1/phase_interrupted。没有PG/HTTP cancel，不写输入文件、不改变原Run预算，不声称未完成单元已提交。双次强制中断或异常收尾不保证完整本地state输出；主入口仍对KeyboardInterrupt给安全错误，不发假成功。

受控真实子进程中断验证：fake research完成第一query及其coverage后，在第三单元显式gate，父进程向自己启动的child发送SIGINT。结果仅section_1 coverage和2条单元events保留、RunMetadata/输入文件不改、llm_calls=0、无Traceback。配置不可用PG地址，验证该独立phase不依赖PG；这不是持久Run取消/恢复或真实研究验收。

验证：`uv run --no-sync pytest -q tests/unit/test_cli_output.py tests/unit/test_cli_run.py tests/unit/test_cli_slice.py tests/unit/test_cli_doctor.py tests/integration/test_mono_cli_dump.py tests/integration/test_mono_cli_phase.py tests/integration/test_mono_cli_run.py`：56 passed in 22.12s；涉及改动Python文件的Ruff/format和git diff --check通过。未重跑全量，不把该目标集称全量或收费模型E2E。T021保持未完成。

## 独立phase实时诊断（2026-10-07）

基线 `3ebe75b`；T021部分。原单阶段CLI在研究执行中只积累events，直到返回结果才可观察；--verbose也没有单元进度。两条新增子进程反例先失败：没有开始/合并日志；受控worker已经等待但父进程仍未收到开始帧。

`phase --verbose`现在在stderr实时输出`debug_unit`的started/merged/failed、阶段、可信plan_units生成的稳定ID、章节和计数。日志明确`persistence=local_only`，只表示局部State合并，不宣称PG提交或取得Evidence；不打印unit.parameters/query、prompt或任意worker诊断正文。共同log输出显式flush，避免等待退出才能观察。最终stdout仍仅单JSON、events及fake结果形状不变；无verbose时不新增日志。

测试1将研究问题/anchors放入私密canary，fake执行10单元并核对20条开始/合并日志、0→10计数、日志没有canary。测试2在plan worker返回前受控gate，真实子进程管道先读到started，再读到worker等待标志，证明观察发生在执行中；随后发送单次SIGINT，退出1/phase_interrupted，仅failed无merged，State保持原样。这些是受控调试入口测试，不是收费模型或持久Run恢复验收。

实现后首次扩大目标集31通过/1失败，原因是新测试误写稳定ID前缀unit_而实际约定为unit-；修正测试前缀，未改领域ID或放宽业务校验。T021完整workers/fake迁移仍未完成。

最终验证（backend）：`uv run --no-sync pytest -q tests/integration/test_mono_cli_phase.py tests/integration/test_mono_cli_run.py tests/integration/test_mono_cli_dump.py tests/unit/test_cli_output.py tests/unit/test_cli_slice.py tests/unit/test_cli_verbose.py tests/unit/test_cli_doctor.py --tb=short`：**54 passed in 23.18s**；3个改动Python文件Ruff/format与`git diff --check`通过。本批未重跑全量；上批927通过不作为本批全量结果。未改Agent/共享Schema/.env/用户数据；docs/implementation保持未跟踪、未暂存。

## CLI与HTTP维护扫描隔离（2026-10-07）

基线`cafea0c`。审计real CLI与HTTP装配差分时发现：CLI复用HttpRuntime.prepare，在默认HTTP维护组合新增后也会启动全库scan_interrupted；即使本CLI TaskRunner有限定scope，共用prepare创建的第二个Runner仍可能改无关Run的失租/排队/取消状态。继承DR4A_DEBUG_RUNNER还可能附带HTTP执行器。此前单独验证TaskRunner scope不覆盖这个组合根风险。

真实CLI子进程反例：隔离PG内先创建无关持租Run并使其租约过期，再启动CLI自己的受控plan模型HTTP等待；运行中检查无关Run完整记录未变。实现前记录被全库维护改为failed，测试失败；测试初稿的变量落点错误先修正，再以正确业务反例验证。另一条prepare反例要求即使HTTP debug开启/传入executor factory，CLI服务准备也不调用该factory、不建HTTP Runner。

修改：内部HttpRuntime.prepare新增显式start_runner=false模式，仅准备原有Service/查询/EventBus，不进行全库维护或自动组合debug executor；real CLI使用该模式，继续自己已有owner+Run限定TaskRunner。HTTP默认prepare仍启动维护或显式执行器，不削弱HTTP取消/恢复；没有新增状态机或持久事实源。

实现前CLI隔离反例失败，prepare反例因不支持该参数失败。验证使用真实PG/MinIO和受控模型HTTP（无收费供应商），结束仅清理本轮fixture资源。没有Agent/共享Schema/Settings/.env修改；research worker尚未加入CLI，完整T021继续未完成。

定向命令：`uv run --no-sync pytest -q tests/integration/test_mono_cli_run.py tests/integration/test_mono_runtime_runner.py tests/integration/test_mono_task_runner.py tests/integration/test_tui_live_http.py --tb=short`：**32 passed in 40.77s**；涉及改动的Python文件Ruff/format与`git diff --check`通过。

随后后端全量`uv run --no-sync pytest -q --tb=short`：**933 passed in 178.01s**。不以全量通过代替真实研究质量/尚未完成的worker验收。用户分支与docs/implementation不暂存、不修改；未推送。

## CLI公开research装配（2026-10-07）

基线`7924c3f`。此前HTTP开发执行器注册plan/research，但real CLI只注册plan，没有Search/Fetch绑定，在research入口即failed。新增受控反例要求research全部10单元提交、seq=14/phase=analyze且在缺失analyze处明确失败；实现前两种plan输出（一次合法、一次修复）均仅到seq=3，反例失败。

`application/debug_runtime.py`抽出共享`PublicResearchExecution`：已有plan/research worker、同一RunDriver/预算/cache/原文Search-Fetch-Parser绑定和资源关闭；CLI传自己的Checkpoint/diagnostic收集回调，用既有限定Runner，不启动HTTP全库执行或共享瞬态队列。HTTP的DebugExecution包装仍强制development，不能由重构开启production debug；CLI生产身份与配置校验不变，不新增生产就绪声明。没有修改Agent prompt/策略、共享Schema、Serializer或正式阶段实现。

parser必须显式HTML或已准备的PDF模式；unknown/unconfigured在start_frozen前退出3/service_not_ready，sessions=0、research_runs=0、模型请求=0。原CLI可能先执行plan再发现后续能力缺失；现在缺parser不会先收费。缺analyze/write/review仍在真实阶段fail，没有空报告。

测试依赖：CLI使用实际main/参数/Service/限定Runner/Driver，模型SDK访问本机受控HTTP，真实隔离PG/MinIO；子进程测试专门将ArxivSearch/BochaSearch.search替换为显式成功空结果，不访问收费或外部搜索。这不是真实论文/供应商Research验收，也没有production fake fallback。

SQL/结果：一个Frozen Brief/一个Run、五章计划；10次search实际受控尝试进入tool_call_attempts与BudgetUsage；五章coverage保留Gap，零Evidence，没有把空检索当研究结论。plan一次或修复两次LLM物理请求都计费50 tokens/次，之后缺analyze，phase=analyze/seq=14，reports=0；真正dump子进程读回同一State。结果events保留本CLI query/section进度。原SIGINT/SIGTERM与无关过期Run隔离反例依然通过。

首轮CLI与debug guard **9 passed in 12.83s**；补parser预检与HTTP生产wrapper反例后扩大验证：`uv run --no-sync pytest -q tests/integration/test_mono_cli_run.py tests/integration/test_mono_runtime_runner.py tests/integration/test_tui_live_http.py tests/unit/test_debug_backend.py tests/integration/test_mono_phase_tools.py tests/integration/test_mono_search_tools.py tests/integration/test_mono_fetch_tools.py --tb=short`：**41 passed in 43.78s**。Ruff与diff检查通过。T021完整fake迁移、后续worker与真实业务验收仍未完成。

最终后端全量`uv run --no-sync pytest -q --tb=short`：**935 passed in 178.30s**，4个改动Python文件format检查通过。未修改用户Agent分支/.env/历史库或docs/implementation，不推送，不将受控结果当实际研究质量证据。

## 独立phase既有原文引用（2026-10-07）

基线76a9272。输入快照带有既有Source时，即使重新取得同一原文，独立phase使用随机目录导致content_object_key不同，正式不可变Source合并拒绝。单测直接构造这一反例，证明未经桥接的Source不能合并。

CLI ResearchDebugTools接收输入State的Sources只读副本；当前query授权的candidate仍必须经过原Fetch下载、解析、完整性与独立目录校验。根据正式register_original计算Source ID后，只有同ID、同hash的公开来源才只读核验输入原文对象（限定research-content UUID/hash key、实际字节hash和size），保留旧raw引用；新parsed引用和所有新写入仍在随机调试目录。未修改共享Fetch的范围规则、Source不可变合并、Agent或Schema。原文缺失/损坏不降级成新引用；其他不可变字段变化仍会被正式合并拒绝。下载与解析成本仍属本次debug_usage，不是持久Run恢复/cache hit，不写PG/预算。

7项单测覆盖原失败与桥接后的正式合并、输入/RunMetadata不变、新/变化内容不读旧对象、损坏/截短/超长字节、private/非法路径拒绝。实际MinIO隔离bucket测试使用受控HTTP原文字节和正式HTML Parser，通过invoke当前query授权路径验证新旧两目录、重复Fetch计数、旧对象删除后的content_missing；共享Fetch对混合目录引用仍content_scope_mismatch。只清理fixture自己创建的bucket，未操作用户业务对象。

定向验证：`uv run --no-sync pytest -q tests/unit/test_cli_research_originals.py tests/unit/test_phase_contracts.py tests/integration/test_mono_cli_phase.py tests/integration/test_mono_cli_originals.py tests/integration/test_mono_fetch_tools.py`：**49 passed in 9.43s**。首次MinIO测试失败仅因断言错误消息写namespace而实际为content_scope_mismatch，纠正测试匹配；未放宽共享规则。T021完整workers/fake迁移与真实Research质量仍未完成。

随后同步verify_research_phase探针：仅接受本次随机目录，或调用者提供的输入Sources中同ID/key/hash公开原文，仍实际读原文并重解析核对Evidence。MinIO测试额外证明默认拒绝旧范围、明确输入引用才允许、伪换其他key仍拒绝。

后端全量`uv run --no-sync pytest -q --tb=short`：**945 passed in 188.07s**。探针最后调整发生在全量进程运行期间，不能把该次已加载模块结果当作最终探针回归；最终代码重新运行上述定向集：**49 passed in 9.38s**，其中包括实际MinIO探针测试。5个改动Python文件Ruff/format与diff检查通过。未调用收费供应商、未改用户历史库/Agent分支/.env/docs/implementation；只读旧原文不意味着持久恢复验收完成。
