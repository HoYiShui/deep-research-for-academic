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
