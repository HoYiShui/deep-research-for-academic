# T018：广播与持久投影 SSE

2026-10-06，HTTP 默认组合根已接入 mono SSE；旧CLI仍使用显式legacy事件，不将旧队列称新契约。

严格五类事件 phase/progress/rework/error/done；每帧有 UUID id、命名 event 与完整JSON；共同字段 session/run/time/seq。progress摘要上限8KiB，同seq不同UUID不能合并。每订阅独立256条队列，慢消费者只断自己的流，不影响研究或其它订阅。

订阅先注册后读owner-scoped PG视图；冻结前409、他人资源404在HTTP headers前返回；bootstrap为当前phase或已提交done。默认5s轮询Run投影，15s注释心跳，Last-Event-ID不播放历史。跨进程进度只能补当前持久phase/done，不保证中间事件全量。对排在bootstrap之前的同seq旧状态重新核对PG，避免到达顺序使状态倒退。

done只能来自已提交终态；completed才提供报告URL。PG读取不可用只发送fatal diagnostic error并断开，不写失败、不开研究、不发假done。客户端断线在ASGI发送中和读取中都清理订阅，不等于cancel。open支持expires_at截止，但实际JWT过期时间的传递须在T053正式鉴权后接入，当前开发匿名无JWT。

测试：11项bus/真实PG订阅反例；独立广播与慢消费者、进度大小/同seq、先注册后bootstrap、迟到completed、owner/冻结前拒绝、跨进程failed投影、PG故障无假done、ASGI发送断线、心跳及未开始迭代的清理、同seq旧状态拦截。另有真实ASGI HTTP终态SSE检查命名done、HTTP headers、Last-Event-ID忽略与清理。全量361 passed，59.02s；Ruff/diff检查通过。

未完成：正式Orchestrator实时细粒度事件发布、JWT认证截止传递、独立TCP+SIGKILL活HTTP验收。T018及M2不勾选；本批受控报告不是cases质量验收。

## 2026-10-07：开发Run逐query进度

正式RunDriver现在发出`query_started`、`query_completed`、`section_completed`三种严格ProgressFrame。身份、phase、seq来自协调器；开始事件不代表结果已保存，完成事件在对应单元Checkpoint事务之后发出。进度数按本phase的稳定单元列表计数（query和coverage均计入），不表示Evidence数量或研究质量。合法空结果/Gap也能完成一个单元。

开发DebugExecution的diagnostic callback已接RunEventBus；TUI现有事件观察器可收到这些帧，无需新的API或Agent输出字段。消息不包含query、原文、凭据或供应商异常正文；观察器异常只记固定安全日志，不导致研究失败。恢复验证成功的已提交单元不重复发出开始/完成事件；断线仍只读GET+订阅，不提供历史progress replay。

反例先验证：新增的成功/失败query测试在旧Driver上因缺少事件而失败。修改后真实隔离PG/MinIO测试证明开始/完成身份匹配、完成seq回查manifest、失败query无完成事件、投影异常不影响最终持久成果。另加中断/失租扫描/显式resume反例，已提交query不再次执行或显示为新尝试。

独立服务进程+实际TCP SSE测试采用受控worker和显式订阅gate，避免依靠sleep碰运气；HTTP收到query完成与章节完成，SQL回查对应seq确有单元manifest。测试不是实际供应商Research或报告质量验收。`test_mono_run_driver.py test_mono_run_tcp.py test_mono_sse.py test_tui_live_http.py`初轮39 passed in 31.69s；新增恢复反例单跑1 passed in 0.83s。Ruff与diff检查通过。

T018仍未全完成：JWT截止传递、全套source_degraded/rework/后续阶段progress以及正式生产worker组合另行验收。当前开发运行器只注册plan/research；不因此声称完整研究或M2/M4完成。未修改Agent、Serializer或共享Schema。

最终回归：`uv run --no-sync pytest -q`：906 passed in 156.38s；TUI `npm test`：12 passed，`npm run typecheck`通过。没有收费Research运行，测试模型/worker均按各测试自身明确的依赖模式，不将全量绿灯代替真实研究效果验收。
