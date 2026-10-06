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
