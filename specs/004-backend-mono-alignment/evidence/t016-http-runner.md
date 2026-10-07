# T016 HTTP Runner 显式组合

2026-10-06，基线 `5fa8f57`。真实 PostgreSQL/MinIO，受控业务模型与阶段输出，不能作为真实研究质量验收。

## 实现和范围

HttpRuntime接受显式 `run_executor_factory(runtime)`，服务、owner查询、SSE投影准备后构建一个TaskRunner。ResearchService只在持久接受之后wake；扫描/容量/租约均复用已有Repository，不使用第二套状态。关闭顺序为Runner停止并记录持租中断→模型关闭→自有PG pool关闭。

未提供factory时保持当前不领取Run的行为，因为完整业务workers尚未实现；不隐式使用测试模型，也不让不完整执行器抢占已接受任务。因此T016仍未全部完成。

上述为2026-10-06组合状态；2026-10-07默认无executor模式增加维护扫描，仍不领取研究，见下方记录。

## 定向验证

```bash
uv run pytest -q --tb=short tests/integration/test_mono_runtime_runner.py tests/integration/test_mono_task_runner.py tests/integration/test_mono_clarify_http.py
uv run pytest -q --tb=short tests/integration/test_mono_runtime_runner.py tests/integration/test_mono_run_driver.py
```

首轮 **40 passed in 17.90s**（运行时3反例）；补全报告闭环与共享fixture后 **26 passed in 13.51s**（运行时4反例及Driver回归）。

最终全量 `uv run pytest -q --tb=short`：**773 passed in 91.70s**；ruff与`git diff --check`通过。

- HTTP创建/确认实际唤醒Runner；执行器异常写PG failed、report返回409、重复scan不自动重跑。
- Lifespan关闭时模型的aclose观察到PG已经failed/interrupted，原Run预算/seq不重置，可显式resume。
- 错误factory不接受HTTP、不领取Run；lifespan依然关闭模型并移除app状态。
- HTTP确认后的新Run经真实Driver/ToolCall账本/MinIO缓存/质量门与原子ReportPublisher，14单元、4阶段转换，done seq=20；PG Session/Run completed，一次受控metered模型尝试，一份needs_more_work报告。
- HTTP GET状态/报告及两次迟到SSE订阅读取已提交事实，不多调用模型、不增加Run attempt。

复用并提取已有Driver受控worker，而非新增一套fake执行链。HTTP使用真实路由但此批为ASGI transport，不声称活TCP或独立进程SIGKILL验收；那些仍在T022。所有PG测试使用唯一隔离数据库；MinIO对象在fixture专属bucket内，不改用户历史库/对象、.env或恢复卷。

## 手动调试维护模式（2026-10-07）

基线 `38c44c1`。设计依据：OPS §4.2、FLOW §4、API §2.6。没有业务executor不等于取消请求可永久无人收尾。

- `TaskRunner(claim_ready=False, execute=None)` 只执行既有PG维护扫描，不创建执行任务、不领取ready、不调用Agent。默认claim_ready=true仍要求可调用executor，不以fake兜底。
- HttpRuntime在手动模式也组合并关闭该扫描器；ResearchService已提交取消请求后wake它。无有效租约的cancelling提交cancelled；有效外部租约保持不变，由持租worker停止或待失租。
- 过期running提交failed/interrupted；显式resume保留原Run/attempt/快照，手动模式仍排ready，不自动付费重跑。原排队超时政策继续有效。
- 202取消响应是接受结果，下一次GET可能已经cancelled；修正旧测试的瞬时时序假设，不人为延迟业务状态。

新增反例实现前因不支持claim_ready失败；实现后定向维护与TUI集成 **5 passed, 13 deselected in 3.09s**。

```bash
# backend
uv run --no-sync pytest -q tests/integration/test_mono_task_runner.py tests/integration/test_mono_clarify_http.py tests/integration/test_mono_runtime_runner.py tests/integration/test_tui_live_http.py
uv run --no-sync pytest -q --tb=short
# tui
npm test
npm run typecheck
```

定向 **50 passed in 23.16s**；后端全量 **927 passed in 158.97s**；TUI **12 passed**，typecheck通过。改动文件Ruff及`git diff --check`通过。首次扩大目标集为47通过/1失败，原因是旧测试要求取消后的GET仍cancelling；修正为接受响应和最终持久状态分别验证后通过。

`test_tui_live_http.py` 使用真实TypeScript客户端→独立TCP后端→隔离真实PG及受控Clarify；确认ready后请求取消，读取终态并核对Session/Run=cancelled、attempt_count=0、tool_calls=0、reports=0。它证明客户端和取消契约，不证明真实研究质量。

验证不运行用户后端、不清用户数据库/对象/卷，不变更Agent、共享Schema或.env。T016完整执行组合仍未完成。
