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

## T060客户端契约交接完成复核（2026-10-07）

基线`5a72717`。上文为历史controller证据；此批补终端入口，不再以其它任务的业务质量要求阻止客户端自身任务完成。T060的范围是客户端HTTP/SSE交接；T039真实业务报告、T053正式认证、T050私有KB仍独立且未完成。

实际`node --import tsx src/main.ts`在本测试创建的POSIX PTY中启动pi-tui，不替换App/Editor/Terminal；父进程经PTY写键盘输入、读取真实渲染输出。后端为独立TCP进程，PG与MinIO为真实隔离fixture；模型/业务报告明确受控，不宣称all-real研究。

完成条目与当前证据：

- 版本/幂等键/确认：api-client和ResearchSession传当前brief_version与请求键；单测证明网络失败重试保留原body/version/key，409只GET不隐式确认。活HTTP脚本/SQL证明Clarify和退回不建Run，明确确认才冻结一份Brief/唯一Run；PTY直接敲/confirm同样成立。
- SourceSelection/SessionView：来源类别及KB IDs按mono序列化；类型补齐knowledge_base枚举并测试服务端404仍原样翻译为ApiError，不替客户端猜测支持。UI的/sources仍只选公开papers/web。GET/open/status读取持久状态，客户端保留返回字段、拒绝未知status和缺少十字段string的确认Brief。
- 错误/身份：匿名请求不发token/cookie；HTTP失败使用标准ApiError/code/request_id。401客户端测试通过，App.error对401明确解释匿名开发模式，不声称验证了正式JWT。
- SSE/恢复：事件ID去重而非seq，同seq多progress保留；失败done不取报告，未知done拒绝；EOF/stop只GET/重订阅，不发启动/恢复。活HTTP+SIGKILL重启验证已提交Run事实、显式resume同Run/seq和缓存成功plan不重发。
- 交互/终态：controller脚本真实HTTP覆盖多轮/退回/确认/进度/运行取消/恢复/报告；PTY第一条从真实界面输入到cancelled，SQL核对attempt=0/reports=0，显示CLI dump提示并正常Ctrl+C退出。PTY第二条看到query_completed、needs_more_work、Report和References；SQL核对completed/seq=20/attempt=1/1Report，运行完成与受控报告质量仍分开。

首条PTY **1 passed in 2.09s**；两条PTY+三条活HTTP客户端：`uv run --no-sync pytest -q tests/integration/test_tui_terminal.py tests/integration/test_tui_live_http.py --tb=short`：**5 passed in 25.46s**。TUI `npm test` **13 passed**，typecheck通过；Ruff/format/diff检查通过。

类型对齐后同一PTY/活HTTP目标集复跑 **5 passed in 26.15s**；本批未重跑整个后端，不将上批935通过冒充本批全量结果。

这组证据满足T060客户端自身的脚本化活后端验收，不把mock客户端测试替代HTTP集成，也不把受控Report替代T039真实报告验收。无收费供应商调用、不变更Agent/共享Schema/.env或用户历史资源；只清测试专属PTY/子进程/数据库/bucket。T018未完成项仍不勾选。
