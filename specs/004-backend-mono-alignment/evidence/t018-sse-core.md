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

## TUI活HTTP运行控制（2026-10-07）

基线`1831606`；T060/T018外围交接部分。扩展现有`test_tui_live_http.py`，使用真实TypeScript ResearchSession/ResearchApiClient、独立TCP FastAPI进程、隔离真实PG与MinIO；复用既有受控Driver/worker和报告fixture，没有另建研究实现或修改Agent/共享Schema。

- 运行中取消：TUI创建/回答/明确确认，等待research；先打开SSE再发cancel，202/cancelling后观察done.cancelled，GET确认resume_allowed=false。报告接口409、重复取消200。SQL核对Session/Run=cancelled、attempt=1、tool_call_attempts=1、reports=0，没有假done.completed。
- 崩溃/显式恢复：独立服务器停在research，父进程只SIGKILL自己启动的server；测试仅把自己独有PG库中的该Run租约时间设过期，不伪造快照/失败/完成状态，也不等待生产90s。新manual服务的真实扫描提交failed/interrupted。TUI重新open读取resume_allowed与seq=3，再显式resume；manual保持ready/attempt=1，证明读取/恢复不偷偷执行。
- 再启动受控执行器：同Run被领取，TUI经SSE看到query_completed和done.completed，再通过HTTP读报告。最终seq=20、attempt=2、仅1个Run/1份Report，tool_call_attempts仍1，已持久成功plan未重复调用。受控报告review_verdict=needs_more_work，不混同运行完成与研究质量。

单跑`uv run --no-sync pytest -q tests/integration/test_tui_live_http.py --tb=short`：**3 passed in 17.72s**。`npm test` **12 passed**，`npm run typecheck`通过。

扩大回归：`uv run --no-sync pytest -q tests/integration/test_tui_live_http.py tests/integration/test_mono_run_tcp.py tests/integration/test_mono_process_recovery.py tests/integration/test_mono_sse.py --tb=short`：**22 passed in 53.58s**。改动测试的Ruff/format与`git diff --check`通过；本批没有全量回归声明。

此验证不涉及收费模型、真实论文或终端布局；T060整体业务/终端交接与T018其它事件仍未全部验收，保持未完成。资源仅fixture生成的`dr4a_test_<uuid>`数据库与`dr4a-test-<uuid>`bucket，结束清理仅这些确切资源；用户历史库、备份/恢复卷、.env和docs/implementation未修改。
