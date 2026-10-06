# T017：共享 execute_phase dispatch（部分完成）

2026-10-06。`application/phase_executor.py` 提供共享PhaseInput→PhaseResult dispatch。ExecutionContext含owner/run/frozen brief hash/config/lease/unit/deadline及受控tool/cancel/diagnostic callbacks；给worker的缩小上下文不含owner、lease、Repository或全局State。

进入worker前校验冻结Brief hash、来源政策、deadline与取消；无worker明确service_not_ready，不返回空成功。克隆输入/config，拒绝worker修改输入或配置；输出phase/unit/input_hash必须匹配，changes严格白名单。执行返回不推进phase、不写DB、不创建报告、不扣未记录预算。I/O完成后若取消，仍可由协调器保存最后安全单元，不在这里丢掉有效result。

9项确定性反例：显式dispatch与原State不变；无worker；修改输入隔离与拒绝；错unit/hash/control字段；取消/deadline前置；冻结Brief/来源scope错配零worker调用；正式plan worker经受控tool callback返回typed PhaseResult。属于dispatch边界，未冒称真实工具/PG执行。

`phase_workers.py` 的plan adapter已连接正式Architect，通过context.invoke("llm",...)调用工具；不直接持有SDK/Repository。未来该callback由T020真实预算/cache实现，再组合到Orchestrator。当前目标集21通过；全量408通过（51.93s）后新增连接反例在目标集中验证，未算进408。

尚未组合到正式Orchestrator/CLI/HttpRuntime；callback的具体预算/cache/diagnostic实现、每单元manifest/checkpoint、Machine控制及恢复属于后续T017/T020/T021。CLI phase调试的无租约context仍需T021专门接入，不能借此claimed context写业务状态。
