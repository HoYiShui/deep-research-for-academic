# T016：单 worker 扫描与持租执行核心（部分完成）

2026-10-06。`application/task_runner.py` 必须显式提供 executor，不自造阶段结果、不 fallback 到 fake。默认 Settings 是90s租约/20s续租/5s扫描；测试为3s/1s/1s，使用真实PG观察续租，不把测试时间称为生产参数。

每个Runner最多一个持租执行Task；Task/heartbeat/扫描器持强引用，退出收集异常并使用固定安全日志。领取容量仍由PG跨进程事务决定。每轮先扫描持久失租/取消/排队超时，再领取ready；failed不会自动重跑。执行器必须先提交终态才返回，单纯return不得当成功。关闭先停止领取、通知executor，超时取消协程，能写PG则保存interrupted；失租旧worker不得写终态，交恢复器处理。外部操作协程必须遵循后续Adapter的取消/timeout契约，不能将TCP断开当作用户取消。

6项真实PG+受控executor：只领取一次且PG实际续租；请求取消后安全停止；关闭保存interrupted且新worker不自动重跑；注入租约过期后中断执行；异常消息不泄露secret；无终态return明确failed且无Report；两个Runner遵守同owner容量（领取/续租/取消综合在第一项）。

目标测试6 passed，5.78s；全量349 passed，51.31s；Ruff及diff检查通过。

边界：尚未接入默认HttpRuntime。T017正式execute_phase/Orchestrator完成后才组合启动；因此现有默认HTTP接受的ready Run仍不执行，不能把本批称为US2完成。取消/恢复/报告的HTTP事务已可用，但自动停止扫描、SSE与真实流水线尚待组合。T016不勾选。
