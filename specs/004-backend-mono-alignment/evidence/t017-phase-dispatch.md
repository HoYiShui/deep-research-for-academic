# T017：共享 execute_phase dispatch（部分完成）

2026-10-06。`application/phase_executor.py` 提供共享PhaseInput→PhaseResult dispatch。ExecutionContext含owner/run/frozen brief hash/config/lease/unit/deadline及受控tool/cancel/diagnostic callbacks；给worker的缩小上下文不含owner、lease、Repository或全局State。

进入worker前校验冻结Brief hash、来源政策、deadline与取消；无worker明确service_not_ready，不返回空成功。克隆输入/config，拒绝worker修改输入或配置；输出phase/unit/input_hash必须匹配，changes严格白名单。执行返回不推进phase、不写DB、不创建报告、不扣未记录预算。I/O完成后若取消，仍可由协调器保存最后安全单元，不在这里丢掉有效result。

9项确定性反例：显式dispatch与原State不变；无worker；修改输入隔离与拒绝；错unit/hash/control字段；取消/deadline前置；冻结Brief/来源scope错配零worker调用；正式plan worker经受控tool callback返回typed PhaseResult。属于dispatch边界，未冒称真实工具/PG执行。

`phase_workers.py` 的plan adapter已连接正式Architect，通过context.invoke("llm",...)调用工具；不直接持有SDK/Repository。未来该callback由T020真实预算/cache实现，再组合到Orchestrator。当前目标集21通过；全量408通过（51.93s）后新增连接反例在目标集中验证，未算进408。

尚未组合到正式Orchestrator/CLI/HttpRuntime；callback的具体预算/cache/diagnostic实现、每单元manifest/checkpoint、Machine控制及恢复属于后续T017/T020/T021。CLI phase调试的无租约context仍需T021专门接入，不能借此claimed context写业务状态。

## 补充：正式 plan 的持久调用入口（2026-10-06）

`application/phase_tools.py` 把 phase-scoped callback 绑定到 T020 的真实账本/缓存。协调器提供明确的 ModelBinding（adapter/provider/model/revision/token reservation）和组合根共享进程信号量；worker 只能提交本阶段 `{phase,prompt}`，不能覆盖 terminal reserve、replay permission、token reservation、provider 或知识范围。

调用身份包含完整 prompt、PhaseInput semantic hash、冻结模型及 prompt 版本、SourcePolicy 与 knowledge_snapshot。binding 版本、Brief/来源错配在外部 I/O 前拒绝；未注册 search/fetch/analysis 明确 service_not_ready，不回退旧适配器或 fake。LLM 只调用 `complete_metered`，工具计数由 Service/PG 决定。

完整模型响应和实测用量作为缓存内容。stop_reason 非 end_turn、供应商模型不符及计量不符在持久记录用量后拒绝，不因校验失败隐藏已支付的调用；同 prompt 读到相同无效结果仍拒绝但不重发。schema repair 更改 prompt 时产生另一语义调用和用量。

13项真实PG/MinIO与受控模型测试：正式Architect→plan_worker→PhaseExecutor→PhaseTools→Service→PG/MinIO；相同输入两次dispatch只发生一次受控模型调用、70 tokens、5章结果，Checkpoint seq仍为1且无Report；worker越权、binding错版、不同prompt、截断/错模型缓存、未注册工具；不同PhaseTools实例共用根信号量，不能各自再增加并发容量。目标集 **13 passed in 3.93s**。

初始12项加入后的全量 **486 passed in 64.20s**；最后补充共享信号量反例后目标集13项通过。这里的模型为明确fixture，不冒充真实SDK。配置里的未配置 revision仍需T056真实readiness/版本收敛，ModelBinding匹配已有字段不证明revision已经锁定。

包含最后共享并发反例的最终全量回归 **487 passed in 63.35s**；新实现与测试ruff检查通过。原PG与无关文件未修改，未push。

尚未组合正式Orchestrator的unit_manifest/Checkpoint/Machine、默认Runner或CLI；其他工具随对应worker继续接入。T017/T020仍不勾选。

## 补充：逐单元完整快照与可验证跳过（2026-10-06）

- `application/orchestrator.py` 新增正式 `RunUnitCoordinator`；旧Orchestrator仍由旧CLI使用，未伪装成新设计。协调器从PG当前seq读取完整State，公开lease检查使用PG时钟/owner/token，Executor/context必须显式提供且匹配当前执行权限。
- `phase_units.py` 的 UnitScope 由协调器提供章节/分析要求/参数，WorkerContext仅得到独立克隆；worker修改范围仍被拒绝。模型call identity现在可包含可信scope，防止同PhaseInput下不同单元范围碰撞；旧无scope调试callback保持明确边界。
- Worker返回的PhaseResult必须通过目标范围纯合并；空plan、不合法回链或上下文owner/run/token错配不能提交。阶段保持不变，完整Checkpoint seq+1，与Run.seq及Session投影同一租约事务。
- 为落实“有效result_hash”，细化mono MODEL的UnitResult，增加 `result_ref: ContentRef`。结果envelope仅包含可信scope、输入hash与PhaseResult，不复制全部输入事实；MinIO先真实保存/验证，PG事务后才保存manifest/合并事实。提交失败可能留下不可变orphan，但不能当已完成单元。
- 恢复跳过要求：读取/校验结果对象、scope/input/result身份、该单元提交前后的不可变Checkpoint，重做纯合并并确认完整post-state一致。缺失/损坏、同ID不同scope明确失败，不重复收费执行已提交单元。
- 结算ledger与State预算在同提交事务读取/校验；cancel在I/O完成后允许最后安全单元落Checkpoint，但无Report/phase推进。旧租约不能提交；双提交只有一个expected_seq成功。投影只在提交后发出，投影异常只记安全诊断，不把已提交Run改failed。

真实PG/MinIO、明确受控模型目标集 **14 passed in 4.80s**：正式plan→预算/cache→单元对象/manifest/seq；新lease无worker也能验证跳过；缺失对象不重跑；篡改scope；SQL故障后重试复用已付费结果；空计划；owner/run/token越权；开始前取消；I/O后取消安全提交；失租；并发seq；投影异常不影响PG事实。

初始13项加入后全量 **500 passed in 71.40s**；最后补充投影异常反例的目标集14项通过。模型不是真实SDK，不能据此宣称研究报告已闭环。

最终完整回归 **501 passed in 69.77s**；本轮修改ruff和`git diff --check`通过，`mdbook build docs`成功（工具提示mermaid preprocessor编译版本0.5.0与当前mdbook0.5.4差异，非构建失败）。原PG/原型仓库/无关文件未修改；未push。

T017仍不勾选：phase单元清单规划、Machine转换/返工、报告质量门及默认Runner/CLI组合尚未完成；T022完整独立HTTP/返工恢复仍待补。

## 补充：稳定单元规划与独立阶段推进（2026-10-06）

`application/phase_units.py` 的 `plan_units` 明确 plan/write/review 整阶段单元、research 每章节 query 与 coverage 单元、analyze 每 requirement 或明确 skip 单元。ID 包含 Run、phase、返工轮次/终止原因与可信范围，不包含变化中的预算、输出、draft_version 或 lease；恢复不能因为前一单元的输出而重新收费生成同一工作。新返工轮次及最终收缩使用不同 ID，终止收缩禁止再次检索/计算。

`domain/research/machine.py` 新增正式 typed 决策，不使用旧 dict 的未知 issue fallback。正常推进验证五章计划、每个 ClaimSpec 的证据或 Gap、每个分析要求的 ComparisonSet、无分析要求的明确 skip 理由，以及下一 worker 的实际输入前置。Review 必须对应当前 draft_version；resolved issue 必须有当前版本的核验与说明。返工保留所有目标，选最早阶段，幻觉撤销未核实 Claim；预算/时限/轮次限制进入一次最终收缩，不升级模型 verdict。Review 返回交付候选不等于 done；仍需报告质量门与原子发布。

`RunUnitCoordinator.advance_phase` 在当前租约下验证全部所需单元对象及历史快照，再单独提交 Machine 转换。例如 plan 单元 seq=2、research 入口 seq=3；提交失败或两者之间取消不得提前切换。Projection 仅在提交之后。预算视图增加 `pending_attempts`，防止零 token 的未完成分析预留绕过单元/阶段结算拦截。

确定性 Machine 与真实隔离 PG/MinIO、明确受控模型目标集 **39 passed in 7.85s**：包括前置缺失、未知 issue、当前版本 Review、多目标路由、返工上限、稳定 ID；未完成单元/缺失对象禁止推进、SQL 失败回滚、取消边界，以及未完成零 token 分析预留禁止提交。同一缓存结果恢复后不重复模型调用。

未冒称真实业务报告：默认 Runner/CLI、完整 Run Driver、真实 research/analyze/write/review worker 与报告发布质量门仍待组合；T017 不勾选。原 PG 数据及参考原型不修改，模型为受控 fixture。

包含全部新增反例的最终全量回归 **526 passed in 71.55s**；修改文件 ruff 与 `git diff --check` 通过。测试连接隔离临时 PG，使用本轮唯一测试库/MinIO bucket，不修复或清理原数据库。

## 补充：完整阶段 Driver 与显式 Runner 组合（2026-10-06）

`application/run_driver.py` 新增 `RunDriver.execute`，根显式提供 PhaseExecutor、真实缓存、冻结模型 binding、进程共享模型 semaphore 与报告 publisher。Driver 装载 PG 当前完整 seq，逐单元执行/验证跳过，再单独提交 Machine 路由；包含返工循环，不按最高 phase 恢复。ToolCallService 仅更新同执行权限下的 phase/seq cursor，不更换 owner/Run/session/lease/config/brief，不重置 monotonic clock；新租约创建新 Service，elapsed offset 从 Checkpoint/持久 ledger 的较大值恢复。

Driver 区分用户取消与进程 shutdown：前者最后安全单元提交后 finish_cancelled，后者抛取消交由 TaskRunner 保存 interrupted。单元/阶段/发布之间均重新检查 PG 租约与取消，阶段推进或 publisher 的取消竞争不伪装 execution_failed。publisher 返回后还须读取 Report、done Checkpoint、Run/Session.completed 四份事实才发完成投影；publisher 空返回、审核候选、诊断或 projection 都不能代表完成。

TaskRunner 保留脱敏的已知失败原因；tool_call_uncertain 可显式恢复但不会自动重跑，config/schema 不兼容禁止恢复。未知异常仍用安全通用失败，不暴露 provider 文本。

真实隔离 PG/MinIO、明确受控输出目标集 **32 passed in 18.30s**（Driver 20 + Runner 9 + SIGKILL 3）：完整五阶段 14 单元、四阶段转换、seq=20 四事实发布；一次目标返工到 write/review、seq=24、全局 draft_version=2；取消前置/单元完成后/阶段竞争/发布竞争；publisher 空返回或故障；完成 projection 故障；计时/预算跨阶段；cursor 权限篡改与回退拒绝；已提交单元恢复不再执行 plan。

新增独立子进程强杀窗口：子进程使用正式 plan worker、受控 metered 模型与正式 Driver；在 unit seq=2 已提交、phase 仍 plan、准备验证结果进入阶段转换时打印同步标记并阻塞。父进程 SIGKILL（无 Python 清理），PG lease 过期扫描后不自动领取，显式 resume 同 Run 到 lease_token/attempt_count=2，再由 Driver 完成 seq=20。恢复期间无 plan worker 调用；工具物理记录仍一次、70 tokens，Report 一份。不是 graceful cancel 的替代证明，也不声称外部 SDK 真实业务完成。

仍未启用默认 HTTP 自动执行：真实 research/analyze/write/review worker、其他工具 binding、确定性报告 serializer/质量门、预算提前耗尽的最终收缩及 CLI 正式切换尚待实施。publisher 为明确事务 fixture，不冒充生产报告质量门；T017/T016/T022 均保持未勾选，完整 HTTP/SIGKILL 返工窗口仍需后续证据。

最终完整回归 **550 passed in 79.34s**；本轮实现/测试 ruff 与 `git diff --check` 通过。原 PG、原型仓库与无关 `docs/implementation/` 不修改；未 push。
