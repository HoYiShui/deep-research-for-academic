# T016 HTTP Runner 显式组合

2026-10-06，基线 `5fa8f57`。真实 PostgreSQL/MinIO，受控业务模型与阶段输出，不能作为真实研究质量验收。

## 实现和范围

HttpRuntime接受显式 `run_executor_factory(runtime)`，服务、owner查询、SSE投影准备后构建一个TaskRunner。ResearchService只在持久接受之后wake；扫描/容量/租约均复用已有Repository，不使用第二套状态。关闭顺序为Runner停止并记录持租中断→模型关闭→自有PG pool关闭。

未提供factory时保持当前不领取Run的行为，因为完整业务workers尚未实现；不隐式使用测试模型，也不让不完整执行器抢占已接受任务。因此T016仍未全部完成。

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
