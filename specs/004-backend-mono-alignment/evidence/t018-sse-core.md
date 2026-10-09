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

## TUI响应正文中断重试（2026-10-08）

基线1b81be1。API client将response.json的所有异常都归为contract_error；若后端已提交且200 headers已收到、响应正文在传输中断开，ResearchSession会清除pending，/retry无法沿用原幂等键。这是消息传输的不确定窗口，不是服务端返回完整非法JSON。

真实本机TCP测试先接收POST并记录body/key，再发送200 headers和部分JSON，声明较长Content-Length后主动断连接；实现前抛contract_error，network_error反例失败。新增responseJson仅对SyntaxError归为完整JSON契约错误，传输/中止读取则为retryable network_error；错误HTTP响应正文的传输中断同样处理，完整非JSON错误页仍保留原HTTP错误fallback。不自动重发、不调用resume/freeze，现有ResearchSession手动/retry复用pending。

200与503两种正文中断测试均证明首次仅一次POST、尚无客户端SessionView；随后即使本地sources改动，显式/retry仍保持原body和Idempotency-Key，得到同一session响应，成功后pending清除。完整非法JSON仍contract_error/非retryable，既有结构化401/SSE/版本冲突测试不变。该TCP服务器显式模拟已接受请求，不能当作真实PG提交/业务研究验收；端到端活后端回归另行执行。

TUI `npm test` **16 passed**、`npm run typecheck`通过；初次typecheck因assert将view缩窄为undefined导致测试代码never，改从retry返回值断言身份，未放宽产品类型。backend `uv run --no-sync pytest -q tests/integration/test_tui_live_http.py tests/integration/test_tui_terminal.py`：**5 passed in 27.33s**，使用真实TCP/PG/MinIO、受控模型/Report、实际PTY，覆盖原确认/取消/SIGKILL恢复/展示路径。diff检查通过；本批不宣称全后端或真实报告质量通过。未改Agent/Schema/.env/用户资源，只清fixture专属数据库/bucket/子进程。

## TUI迟到状态读取（2026-10-08）

基线cb573d5。代码审计发现refresh和observer的两处GET均直接覆盖view；并行GET/SSE时较早发出的响应可能迟到，覆盖较高seq或较新Brief。refresh在Session切换后虽不赋值，却仍返回旧响应，resume会依据这个旧响应发送变更。

ResearchSession集中读取采用acceptStatus：仅同Session合法身份，保留更高brief_version；既有冻结Run不由迟到null Run回退，Run身份变化明确contract_error，同Run低seq不覆盖当前view且调用者收到已知较新view。refresh在会话切换后抛本地aborted，禁止resume继续针对旧会话。open先验Session身份，observer bootstrap/EOF后GET同样使用该读取规则。相同seq的状态仍按后端GET读取，不新建客户端状态优先级或终态机；SSE done只保留seq，终态仍须GET。

6项显式受控Promise/事件单测：两个refresh的返回顺序逆序；observer bootstrap等待时手动refresh先得到seq5；旧Brief/null Run；done seq2后旧GET seq1不能擦掉seq或伪造完成，下一GET确立completed；错误Session/Run身份；Session切换时等待中的resume无POST。观察中只GET/订阅，不发新建/确认/自动恢复。此竞态证据是客户端受控测试，非真实网络延迟或研究质量验收。

TUI `npm test` **22 passed**、typecheck通过（初次测试将assert.fail直接作为unknown回调导致类型不兼容，改显式回调，未放宽产品类型）；backend活TCP/PG/MinIO、真实PTY回归 `uv run --no-sync pytest -q tests/integration/test_tui_live_http.py tests/integration/test_tui_terminal.py`：**5 passed in 27.18s**。diff检查通过。本批未跑全后端，未修改Agent/契约/.env/用户历史库或docs/implementation，未推送。

## HTTP/TUI 超时诊断（2026-10-09）

基线c205094。上一批两次完整回归分别在CLI probe 20秒、HTTP SSE 15秒、TUI query_completed 10秒观察窗口超时，而原文件补跑通过；失败缺少PG推进状态，尚不能区分执行慢、租约问题或事件观察停滞。不改Prompt/Schema/业务/验收deadline，不将未知原因叫作已修复。

新增test-only `tests/support/run_diagnostics.py`，只在原用例失败时、子后端尚未关闭且fixture PG尚未删除前读持久状态：Session/Run ID/status/revision、phase/checkpoint_seq/attempt/lease到期、checkpoint提交时间、ToolAttempt分组与PG连接等待类型。整体诊断最多2秒、最多20个资源/100个checkpoint；拒绝非本轮 `dr4a_test_<uuid>` 库，不读用户历史库。查询不选Brief/query/state/正文/config/object key/凭据/SQL文本或任意Failure文字；依赖故障仅记异常类型。原异常继续抛出，不以诊断结果代替门禁，不扩展运行允许时间。这个辅助诊断不是CLI公开trace、生产监控或完整readiness实现。

3项新测试使用真实隔离PG核对状态/seq、正文canary不输出、查询不改变checkpoint；确认诊断原样抛原异常、非测试库提前拒绝及敏感依赖异常文本不输出。与原活HTTP/真实PTY文件一起 `pytest -q tests/integration/test_run_diagnostics.py tests/integration/test_mono_run_tcp.py tests/integration/test_tui_terminal.py --tb=short`，**7 passed in 34.31s**。4个Python文件Ruff/format、git diff --check通过。

本轮完整当前工作区 `PATH=<已有fnm Node v24.13.1>/bin:$PATH UV_CACHE_DIR=/private/tmp/dr4a-uv-cache uv run --no-sync pytest -q --tb=short`，**1211 passed in 304.29s**。包含真实Compose PG/MinIO/Standalone及既有受控模型HTTP/TUI，不是Research业务报告质量验收。本次没有复现原超时，因此根因未确认；之前两次失败证据仍见[管理HTTP留痕](t040-t041-knowledge-base.md#2026-10-09管理-http分页与创建恢复)，不以这次green抹除。全量是当前工作区结果，包含之前未提交trace/search-router；本次仅提交诊断与留痕，不夹带那些改动，不宣称从本次clean commit单独重跑过全量。

没有新增/重启Docker容器或改数据卷；只清fixture自有库/bucket/子进程，保留docs/implementation。T056/T061与其他未完成任务不勾选，完整目标保持；Research真实工作流/Prompt仍暂缓，接下来推进T049物理删除/恢复闭环。

## 2026-10-09：读取快照与 Run 领取竞争

基线c162068。上批完整回归1231通过、2项HTTP/TUI超时；补跑仍出现SSE超时。只读诊断显示一例Run一直ready/attempt=0，另一例最终completed/seq=20但首次领取比创建晚约10秒。这些是失败现场，不足以单凭时间证明每次超时的同一根因。

代码核对发现SessionView、CLI checkpoint dump和Report投影在写事务中对Session执行FOR UPDATE；ready Run领取使用FOR UPDATE OF s SKIP LOCKED。新增真实隔离PG竞争测试：读取Session后暂停、另一个连接领取ready Run。旧实现两个投影均使claim返回None；解除读锁后才可领取。这证明了一个实际竞争路径，不能把GET称为不会干扰调度。

新增UnitOfWork.snapshot，由PG强制read-only REPEATABLE READ，三个投影在同一短快照里读取Session/Run/Brief/Checkpoint/Report，去掉读接口FOR UPDATE。写事务隔离、父子锁、租约token/revision和原子发布不变；SSE bootstrap/poll与CLI dump复用原投影，无轮询间隔或15/20/25秒验收deadline修改。Fake也保持独立只读数据快照，不把读快照提交成覆盖并发写入的新事实。

真实PG测试核对事务隔离/read-only、快照内SQL写入拒绝、并发领取成功、同一快照保留旧一致状态、后续GET看到新running/revision。Fake契约检查同快照不受并发提交影响、禁止写入、跨store/关闭后句柄拒绝。新增fake测试首轮因fixture未递增brief_version且修改不可变query失败；修正为合法Clarify版本演进，未放宽产品校验。

修改后首批投影/SSE/dump目标31项通过（10.98秒）；原HTTP/TUI/PTY目标8项通过（46.23秒），fake及PG快照单独14项通过（1.00秒）。完整工作区回归与独立暂存快照结果待下方补充；不以目标通过宣称所有历史延迟均已消失、T018 JWT/事件缺口或T056生产readiness已完成。没有收费Research或Prompt调整，没有重启Compose或接触用户历史数据。

完整当前工作区首轮 **1238 passed, 1 failed in 284.64s**，唯一失败为上述修正前的fake fixture；该进程在修正前已加载测试，不能把单测补跑称为同一轮全绿。独立暂存提交通过checkout-index导出，不包含未提交search-router/trace；首次目标集因缺tsx出现6失败/35通过，补已有node_modules后因用例固定调用backend/.venv出现1失败/40通过。两者均是验证目录依赖准备缺失，未改应用或放宽测试。为快照链接原本已安装的Python/Node依赖及backend/.env（不复制凭据），最终八文件目标集 **41 passed in 54.21s**，包含真实PG竞争、SessionView/SSE/CLI dump、独立TCP及真实pi-tui PTY；测试源码与应用源码均来自暂存快照。TUI自身 **22 passed**、typecheck通过。

最终完整当前工作区 `PATH=<已有fnm Node v24.13.1>/bin:$PATH UV_CACHE_DIR=/private/tmp/dr4a-uv-cache uv run --no-sync pytest -q --tb=short`：**1239 passed in 278.08s**。这是包含先前未提交trace/search-router的当前工作区全量结果，不声称clean commit单独全量；clean暂存源码的独立门禁为上文41项。Ruff/format及diff检查通过；Compose仍为原五个healthy服务，没有新增/重启容器或改卷。验证目录移到本机Trash，可恢复，不删除用户文件。T016/T018/T056剩余验收与Research真实质量仍各自保留，不能用此次green勾选。
