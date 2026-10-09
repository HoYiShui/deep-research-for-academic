# T040 / T041：知识库事实与原子版本发布底座（部分）

日期：2026-10-08；基线 `3e44ca8`。依据 MODEL §5–6、FLOW §5–7、API §4、OPS §5。本批不接管 Agent；不宣称完整知识库入库、向量检索或真实 PDF 成果闭环。

## 已实现

`application/knowledge_models.py` 包含 KB/Document/Version/Job/Attempt/Progress/Chunk、RetrievalResult/SourceMetadata/Trace/VectorHit/RankedHit。复用严格 Record/Location/Failure；拒绝额外字段、bool 冒充计数、非有限得分、缺定位、重复批次、无租约 processing、缺失尝试历史与未发布 active 版本。private 默认；历史 completed Job 不要求版本永远 active。

新增追加迁移 `0005_mono_knowledge.sql`（0003/0004 已被工具调用使用，不重编号）。五张表、owner 内活名称唯一、复合父子 FK、内容身份唯一、Document 单 active 版本及单活动 Job、Chunk ordinal 唯一；延迟约束验证 Document.active_version_id 与版本 active 状态同一事务一致。没有 cascade 删除成果或改写历史表。原迁移测试的版本总数由 4 更新到 5，其他迁移原子性/独立进程竞争测试保留。

`KnowledgeRepository` 与 Research 共用同一个 PostgresResearchStore/UoW，不建立第二个可写事实源。owner 查询、上传后 submit、锁 KB/Document/Job、领取带递增 fencing token 的尝试、失败记录、原子 activate。内容重复复用原 Version/Job；显式 Document 冲突返回 content_identity_conflict；活动 Job 冲突 document_busy 且回滚候选版本。

activate 校验资源状态、当前租约/取消、profile、连续非空 chunk manifest、对象 scope/hash 与不可变 Chunk 身份；同事务插 Chunk、旧版本 retired、新版本 active、更新 Document pointer/revision、完成 Job/尝试。结束写 Job 时再次以 SQL 时钟检查 token/expiry/status/cancel，不能仅在长事务入口检查租约。任一失败回滚所有发布事实，旧 active 保留。

新的 KB ContentStore 键为 `knowledge-content/{owner}/{kb}/{version}/{hash}`；只允许指定 KB/版本前缀清理，不允许 owner 根前缀。保留已有 documents/research-content 内容 Adapter 命名空间兼容性，不把它当新 KB 的 owner 命名空间。MinIO 字节仍按实际 hash 验证；服务不接受客户端存储键。

## 验证与发现

`uv run --no-sync pytest -q tests/unit/test_knowledge_models.py tests/integration/test_mono_kb_lifecycle.py tests/integration/test_mono_migrations.py tests/integration/test_mono_document_content.py --tb=short`：**62 passed in 8.33s**。真实独立 PG 和 MinIO；KB/Version/Chunk 是显式 metadata fixture，无真实 Parser/Embedding/Milvus 调用，不能把原子激活的受控成功信号称完整入库成功。

实际验证：跨 owner 不可见；staging 没有可见 Chunk；两个并发重复请求收敛同 Version/Job、没有多余 Document；单活动 Job/复合 FK/延迟 pointer 约束；替换后旧 Job completed/版本 retired 合法；注入发布 SQL 故障回滚；失败替换仍可见旧版本；错 token、已过期、取消、KB/Document 删除屏障拒绝；真实 pg_sleep 延迟 Chunk 写入令租约在事务中途到期，最终发布拒绝并回滚；源 key 的 owner/KB/version/hash 错位拒绝；真实新命名空间字节回读，清理本版本/本 KB 不影响其他 KB/owner。

初轮迁移回归发现两个旧总数断言仍写 4，已更新为新的追加迁移数 5。代码审查发现继承模型字段顺序会使 Job 更新的 `$1` 不再是 job_id，已改为显式绑定身份、不依赖模型字段顺序；真实领取/发布/失败测试通过。没有降低断言或恢复旧语义。

最终稳定代码全量：`uv run --no-sync pytest -q --tb=short`，**1043 passed in 215.08s**。此前一次 **1033 passed in 216.07s** 运行期间继续补了 owner 命名空间与相关反例，不能用它替代最终回归；已重新完整运行。8 个改动 Python 文件 Ruff 与 format 检查通过，git diff --check 通过。

测试只创建并清理本轮 dr4a_test_* 数据库和 dr4a-test-* bucket；不应用迁移到用户数据库、不启动 Compose PostgreSQL、不改 .env、不触碰 docs/implementation。用户调试库尚未由本批命令升级；后续正常显式初始化/Runtime.prepare 才应用新迁移。只读 doctor 会准确将缺少当前迁移的库标为 schema 未就绪。

## 未完成

T040/T041 保持未勾选：creating/deleting 清理租约、cursor/CAS 与恢复扫描、完整 Job heartbeat/progress/cancel/retry、Service 组合/HTTP 公共视图仍待后续。Repository 的 activate 接受 App 已验证对象/索引的成功信号，本批不伪造这项真实依赖验收；实际 Ingestor/版本发布门与索引可读验证属 T044–T047。KB/内容/向量完整删除与迟到写入清理属 T049；真实检索回链与 PDF E2E 属 T048/T052。

## 2026-10-08：Job 控制与过期恢复的 PG 内核

续批基线 `0271dcd`。代码拆分到 `infrastructure/storage/knowledge_jobs.py`，复用同一 UoW、锁顺序和严格 Job/Attempt/Progress 记录，不新增第二套事实源或公共 API。

先运行新反例：Job 控制 9 项因缺少方法失败，过期恢复 2 项同样因缺少方法失败；再实现并验证。新的最终目标集包含本批 14 项真实 PG 测试，以及原生命周期/模型测试：`uv run --no-sync pytest -q tests/integration/test_mono_ingestion.py tests/integration/test_mono_kb_lifecycle.py tests/unit/test_knowledge_models.py --tb=short`，**61 passed in 9.83s**。

- heartbeat 验证 owner、worker、token、SQL 时钟，不能借用或复活过期租约；进度有界/单调，不丢已完成批次，同步保存本次尝试快照。
- cancel 只进入 cancelling，保留活 Worker 的租约并阻止后续入库进度。清理必须持有效 token；其他 Worker 不抢活租约，过期接管递增 token。资源进入 deleting 后仍允许清理/清理 heartbeat，但不能继续入库。
- finish_cancel 必须由 Service 验证外部清理后显式调用；否则 503 且保持 cancelling。PG 事务仅删除本 staging 版本 Chunk 元数据、清解析/manifest 指针、标版本 failed、结束 cancelled，保留原始源 key 和尝试历史。本批测试使用显式受控 cleanup_verified 信号，**没有执行实际 MinIO/Milvus 清理**，不把该信号当物理删除证明。
- retry 必须由 Service 实际读源/hash 验证后调用；资源 active、failed 可恢复或已清理 cancelled、attempt_count<3。保留同 Job/Version/历史，不在接受重试时增加尝试次数；领取时才增加，不能通过重新生成 Job 绕过上限。源已知不可用返回 resume_not_allowed；存储暂不可读应由 Service 保持外部 503，不能用 false 掩盖依赖故障。
- trusted recovery scanner 仅处理过期 processing：活资源写 failed/interrupted，关闭该次尝试、递增 token、版本 failed，不自动重试；删除屏障下转交 cancelling，等待清理而不假宣称完成。两个真实 PG 并发扫描仅一方记录失败，不增加重复尝试。失租 Worker 不可继续提交。

failed 的取消在内部也先取得清理边界；后续 HTTP Service 必须在清理完成后返回契约要求的 **200 cancelled**，不能把本内核的过渡 cancelling 当作新的 failed 取消响应契约。completed 禁止取消/重试；重复 cancelling/cancelled 请求保持身份和历史。

剩余边界：此 scanner 尚未接 TaskRunner；没有实际进程 SIGKILL/外部迟到写入验收，没有持久 KB/Document 清理 cursor/租约扫描，完整 Service/HTTP/idempotency 仍待 T042/T047/T049。T040/T041/T047 不勾选。本轮不升级用户数据库、不修改 Agent/.env/docs/implementation。

续批最终全量：`uv run --no-sync pytest -q --tb=short`，**1057 passed in 227.99s**；3 个改动 Python 文件 Ruff/format 检查与 git diff --check 均通过。本次只有文档证据在全量运行期间更新，受验证的代码与最终提交一致。

## 2026-10-08：Job 应用层与 HTTP 首批

基线 `a8c5db3`。新增 `application/document_ingestion.py`、typed IngestionJobContext/JobAccepted/Port、`interface/router/ingestion_jobs.py`；默认 HttpRuntime 装配私有 MinioContentStore 与持久 Job Service，关闭时回收 Adapter，失败也继续 PG 收尾。未改 Agent、用户 .env 或用户数据库。

GET /ingestion-jobs/{UUID} 返回来自 owner-scoped PG 短事务的一致父子上下文，显式投影公共 IDs/status/progress/failure/timestamps，不含 lease/storage key 或内部 failure.details。retry_allowed 是当前生命周期资格加真实源存在性检查；它不表示 Worker 已执行，不替代 POST 的完整字节验证。已知源不存在为 false；未知存储故障返回脱敏 503，不能假装 missing。

POST /retry 使用空 DTO/Idempotency-Key，先授权，再保留请求租约（120s）或返回原成功响应；源 I/O 在 PG 事务外，60s 上限，检查 owner/KB/version/hash 固定 key、真实 head/流式字节/大小/hash。短最终事务重检当前资源/Job/删除屏障、接受同 Job/Version 重试并原子保存 202 响应；失败释放 reservation。缓存响应也经严格 Schema、HTTP202/Job/资源身份复查，不返回损坏缓存的假接受。并无“幂等缓存自动修好数据”承诺。

取消路由存在但清理 Worker 尚未装配：除已 cancelled 的只读重复响应与 completed 的409外，明确503 service_not_ready，不承诺排入不存在的清理队列、不写假 cancelled。上传/完整取消/TaskRunner仍属于后续 T047。该暂时能力缺口不是对 API 设计的另定语义。

先跑首批 HTTP 测试，5项因缺失 Service 模块失败；接入后5项通过。追加反例发现损坏 cached response 被当202，3项真实PG测试失败；修复为严格投影和身份验证。最终：`uv run --no-sync pytest -q tests/integration/test_mono_ingestion_http.py tests/integration/test_mono_ingestion.py tests/integration/test_mono_kb_lifecycle.py tests/unit/test_knowledge_models.py --tb=short`，**72 passed in 12.85s**，其中11项新的HTTP测试使用真实独立PG/MinIO、受控源字节（只证明源传输，不是有效论文解析）。

覆盖公开JobView、真实源验证/同身份重试、删源后同幂等键仍重放原响应、跨owner在存储I/O前404、缺源409且不接受、篡改源503且释放请求、不泄漏SDK异常/内容/key、存储宕机不是missing、源I/O期间提交真实删除屏障证明未持PG资源锁并拒绝最终retry、损坏缓存拒绝、空body/key验证、未配置取消能力明确失败。T042/T047保持未完成；旧CLI/Management/Retrieval/上传/真实Parser/Embedding/Milvus/完整取消与清理队列未替代。

本批最终全量：`uv run --no-sync pytest -q --tb=short`，**1068 passed in 230.95s**。8 个改动 Python 文件 Ruff/format 检查与 git diff --check 通过。全量运行期间仅更新任务说明和证据，未改变受验证代码。测试只清理本批独立测试数据库与 bucket，未升级用户数据库或操作受保护 PostgreSQL 容器/卷。

## 2026-10-08：管理事务与删除屏障

基线 `f6f0cf7`。新增 `KnowledgeBasePatch` 与 Repository 内部 `KnowledgeManagement` 组合，尚未对外启用 Management HTTP。Patch只允许revision/name/description，拒绝空修改、null name、空白name、超长值、bool revision，以及分类/index profile等不可修改字段；明确省略description保留、显式null清空。数据库短事务owner+ID锁定，SQL revision CAS，成功revision+1；重复name由现有唯一约束翻译为name_already_exists且整事务回滚。并发两个同revision更新只有一个成功。

KB active/creating进入deleting与Document active进入deleting原子保存revision+1、清理起点wait_jobs；同状态重复保持cursor与revision，deleted墓碑重复只读。Document按KB→Document顺序锁定并验证父子关系，不能跨owner/KB操作。KB或Document屏障提交后已有active正文立即不可见；未完成Job的迟到activate和新submit被拒绝。原active pointer、版本、Chunk、Job历史仍保留供后续物理清理定位，不能把本批称为已删除对象/向量。并发Patch/delete不能把deleting复活或清掉cursor。

验证：`uv run --no-sync pytest -q tests/integration/test_mono_knowledge_management.py tests/integration/test_mono_ingestion.py tests/integration/test_mono_ingestion_http.py tests/integration/test_mono_kb_lifecycle.py tests/unit/test_knowledge_models.py --tb=short`，**89 passed in 14.65s**，其中新增17项；真实独立PG，没有真实Milvus删除或MinIO清理声明。4个改动Python文件Ruff/format和git diff --check通过。测试只在隔离数据库内建立或改变资源；不升级用户数据库、不重启Compose PostgreSQL、不改Agent/.env/docs/implementation。

T040/T041/T049继续未勾选：HTTP幂等管理/分页、creating/deleting共同清理租约、Worker调度、索引/对象删除、迟到写入及SIGKILL恢复仍须闭环。本批只建立必要的持久化屏障，不接受缺少清理Worker的公共删除请求。

最终全量：`uv run --no-sync pytest -q --tb=short`，**1085 passed in 228.41s**。全量期间只更新任务/证据，没有改受验证代码；静态检查通过。本批不以其他测试的green结果替代完整删除验收。

## 2026-10-08：清理租约与进程中断

基线 `d7d0bbd`。新增Repository组合 `KnowledgeCleanup`：可信有界creating/deleting扫描（扫描不授租）、KB生命周期共用租约和Document独立清理租约、领取/同Worker重入/活租约排他/过期token递增、续租/释放、revision+token+SQL时钟校验的cursor提交、KB创建完成门。所有表名只来自内部固定分支，Document仍按KB→Document锁定；跨owner/父子不匹配拒绝。

清理cursor记录**下一待处理步骤**：wait_jobs→index→objects→metadata；重复同点且当前revision/token有效时保持revision/updated_at，不能跳步或回退。短事务不执行外部I/O，各步由后续Service验证外部操作后提交。释放租约不清cursor；重启领取保留进度并递增token。SQL在最终UPDATE检查expires_at>clock_timestamp，不仅在事务入口以Python时钟判断。实际事务内pg_sleep使租约过期，进度/续租/释放均拒绝。

creating转deleting保留现有租约，其他Worker不能提前抢占；原创建Worker即使提交partition_verified信号也不能把deleting复活。finish_kb_creation同时验证status/revision/token/未过期与Service成功信号，之后才active并释放租约。本批成功创建测试使用显式受控partition_verified，**没有真实ensure partition验收**，不能据此宣称KB创建端点可用。

新增真正独立Python进程：只连接本轮dr4a_test_*数据库，提交lease+index cursor后保持存活；父测试明确SIGKILL（退出码-9），没有child finally/graceful清理。等待实际租约过期，可信扫描可发现资源，新Worker领取后仍为index、revision不丢、token递增；旧token提交objects拒绝，新token继续objects成功。这证明PG进程中断边界，不等同于外部清理断点或正式TaskRunner重启验收。首轮测试把fixture数据库名误当DSN，子进程在连接前失败；已改为Settings真实DSN+仅覆盖隔离database，未改变断言/降低隔离要求。

目标：`uv run --no-sync pytest -q tests/integration/test_mono_knowledge_cleanup.py tests/integration/test_mono_knowledge_management.py tests/integration/test_mono_ingestion.py tests/integration/test_mono_ingestion_http.py tests/integration/test_mono_kb_lifecycle.py tests/unit/test_knowledge_models.py --tb=short`，**103 passed in 20.44s**，其中新增14项。3个改动Python文件Ruff/format与git diff --check通过；未修改Agent/.env/用户数据库/Compose PostgreSQL/docs/implementation。

T040/T041/T049/T055仍未完成：未组合Management/TaskRunner，不接受没有真实清理能力的公共删除请求；等待旧Job停止、实际index/objects删除、最后PG墓碑事务、创建partition验证及deleted迟到写入巡检尚待实现。扫描和cursor内核不是全生命周期恢复完成的替代证据。

中断恢复检查：先前全量进程handle已丢失且系统没有pytest进程，未假定它成功。重新执行默认配置发现受保护 `dr4a-rebuilt-pg-20261006` 反复重启，日志 `PANIC: replication checkpoint has wrong magic 0 instead of 307747550`；默认回归 **775 passed, 324 errors in 56.18s**，连接5432失败，不是通过。未停止/重建/修复该容器，不改任何原数据卷。

为继续代码验证，用本机已有PG16镜像启动本轮独立 `dr4a-test-pg-cleanup-*`，PGDATA在512MiB tmpfs，无用户volume挂载，127.0.0.1动态端口；仅命令环境覆写DATABASE_URL，不改.env。临时实例设置max/min WAL为64/32MiB、checkpoint_timeout=30s避免tmpfs写满，测试仍按原fixture新建/删除dr4a_test_*数据库。上述目标集在临时真实PG及真实MinIO重新 **103 passed in 12.99s**。这不证明用户调试库已恢复；该库仍须另行数据恢复。

临时实例全量最终 **1094 passed, 5 failed in 177.46s**；5项均在TUI子进程启动时 `FileNotFoundError: node`，没有进入业务断言。确认已安装fnm Node v24.13.1，显式将其installation/bin加到本轮命令PATH，并保持同一临时PG。原5项不改代码/断言补跑：`pytest -q tests/integration/test_tui_live_http.py tests/integration/test_tui_terminal.py --tb=short`，**5 passed in 24.15s**。这是全量加失败项补跑的组合证据，不能记成一次1099项全量通过。3个改动Python文件Ruff/format与git diff --check通过。临时PG仅作本goal隔离验证实例，未将.env或用户Runtime默认连接改向它；原恢复库仍不可用。

## 2026-10-09：管理 HTTP、分页与创建恢复

基线 `9fb0bfc`，本批仅推进外围知识库应用层，不运行真实 Research/付费模型，不改 Agent Prompt。设计来源 API §4.1、MODEL KB/Document/Version、FLOW 创建流程与 OPS 生命周期租约。仍使用现有 Compose PG/Standalone；不新开容器、不重启服务、不改用户数据库或救援卷。

实现落点：`application/knowledge_base_management.py`、typed create/view/Repository Port、`storage/knowledge_management.py`/`knowledge_cleanup.py` 与 `interface/router/knowledge_base.py`，由 `HttpRuntime` 组合。正式管理 HTTP 使用复数 `/knowledge-bases`；旧单数 HTTP 上传/搜索/内存注册表入口已退役为404，不能再提交未受追踪的上传 Task 或把删除内存记录当物理删除。旧CLI仍待T051，未声称所有legacy代码已移除。

创建流程：短PG事务保存creating KB与幂等resource_id绑定 → 共用KB生命周期lease/token → 事务外真实ensure_schema/partition（SDK有界、heartbeat保持lease）→ 同事务active revision+1与原201缓存提交。模型无需参与。异常只返回安全index_not_ready+kb_id，并持久化Failure、不标active；释放请求执行租约但保留resource_id/hash，下一次同key/body沿用原KB。绑定不可换ID、不可被普通release抹除；新key同名409、改body同key409、活请求409+Retry-After。成功重放不调用Milvus，保留原响应即使后续实体更新。

GET/PATCH/list按owner和父子ID授权，description省略/显式null区分，revision CAS与幂等缓存同事务提交，分类不可修改。列表使用(created_at,UUID) keyset与多取一行；cursor为有界base64严格Schema，绑定owner/类别/KB/Document/status，不是授权凭证，不接受SQL表达式；跨scope、非法cursor和limit=0/101均422。KB默认不列deleted，墓碑可按ID/状态读取。Document详情最多100版本并使用现有Job公开视图，不暴露leases/storage keys/Failure.details。查询staging元数据不等同于检索公开staging正文。

TaskRunner新增可选维护回调：仅持一条有强引用、完成异常观察、关闭取消的维护Task，扫描PGcreating身份，不建立第二套可写队列。外部索引I/O不阻塞Run扫描或heartbeat；失败等待下一扫描间隔，不立即热循环。owner/Run-scoped CLI Runner禁止挂全局维护，HttpRuntime.prepare(start_runner=False)不启动扫描。恢复成功只提交KB状态，原HTTP请求下一次重试才缓存201。

真实验收：`tests/integration/test_mono_kb_http.py` 使用独立PG库和现有Standalone的本轮UUID partitions；创建验证真实has_partition。MinIO只用于邻接Job/源验证及隔离fixture，本批不宣称文档上传/解析/正文检索闭环。另加3项TaskRunner维护观察/不阻塞/关闭与CLI拒绝测试。直接HTTP覆盖归属、输入校验、失败持久绑定/恢复、创建和属性幂等、同时间分页、Document/Version/Job安全视图、墓碑只读政策及未接删除不假接受。

独立TCP测试用标准HttpRuntime、真实PG/Milvus和NoModel（任何模型调用会失败）。真实ensure_partition后子进程输出精确断点marker并SIGSTOP，父测试SIGKILL（-9），没有child finally。此时PG仍creating，真实分区已存在、原请求绑定持久。只把该隔离库的两条lease时间推进为过期以免等待120秒，再启动新独立后端，TaskRunner扫描接管→active/token2；同key POST201仍原KB，PATCH/GET/list/documents一致，tool_calls/sessions均0。该测试证明真实崩溃后的creating恢复，不证明删除断点或一般跨存储exactly-once。首轮109项目标集中1项失败：Uvicorn access log先进入stdout，断点marker断言未到；test-only KB server关闭access log后原测试1项通过，没有改业务断言。

邻接验证（新增输入/墓碑/CLI准备7项前）：`uv run --no-sync pytest -q tests/integration/test_mono_kb_http.py tests/integration/test_mono_task_runner.py tests/integration/test_mono_knowledge_management.py tests/integration/test_mono_knowledge_cleanup.py tests/integration/test_mono_ingestion_http.py tests/integration/test_auth_guard.py tests/integration/test_mono_http_errors.py tests/integration/test_slice_kb_search.py tests/integration/test_slice_kb_ingest.py tests/integration/test_mono_transactions.py`，**123 passed in 29.95s**。Ruff与git diff --check通过。继续全量回归后在下方记录最终结果，不能把上述邻接结果当全部任务完成。

边界：T042/T049/T055不勾选；完整上传Worker、BGE/Parser、检索Service、删除对象/向量/旧Job协调、最后墓碑事务和deleted迟到外部写入巡检仍缺。公共DELETE在校验归属后明确503、不改变原active状态；不能凭已有PG删除屏障提前202。未实现的上传/检索没有启用legacy回退。测试只删除隔离数据库、fixture bucket和本轮已确认UUID partitions，未删现有内容/报告/数据卷；分区复核仅_default。

提交独立性复核：只导出本批暂存树 `f92f0f141f550d8ae818da59a7dc22d46f218f82` 到本轮临时快照，复用已安装venv，真实配置只经进程环境传入而不复制.env；上述目标集连同最终7项新增用例 **130 passed in 32.91s**。未包含工作区未提交的search-router/trace实现，证明本批提交不是依赖未提交代码才通过。导出前曾发现按零上下文选择bootstrap hunks会错放插入行、快照SyntaxError；已仅修正暂存文件并检查AST，没有覆盖工作区既有改动。最终只有KB相关bootstrap hunks暂存，之前search-router改动仍原样留在工作区。

首次最终工作区全量 **1207 passed, 1 failed in 297.37s**。失败是已有 `test_mono_run_tcp.py::test_probe_cli_creates_answers_then_requires_explicit_approval` 子进程20秒观察超时，没有完整终态诊断，不能断言确切根因。原文件2项单独补跑 **2 passed in 25.91s**，没有放宽deadline或改业务/Prompt。此时仍不是一次完整全绿，继续独立重跑全量并记录结果。全量期间除一个旧测试文件的纯格式化外，未改受测行为。

第二次完整工作区回归 **1206 passed, 2 failed in 302.73s**，此次CLI probe通过，另两项分别在活HTTP SSE 15秒观察与TUI等待query_completed 10秒超时：`test_confirm_live_driver_report_and_reconnect_after_process_restart`、`test_actual_tui_renders_live_progress_and_controlled_report`。原文件组合再跑 `pytest -q tests/integration/test_mono_run_tcp.py tests/integration/test_tui_terminal.py --tb=short`，**4 passed in 38.59s**，仍未改任何deadline/业务/Prompt。Docker五个原Compose服务healthy、Milvus复核仅_default；没有遗留本轮测试容器/分区。**全量不记为通过，根因尚未确认**；本批提交基于独立暂存快照130项与目标真实依赖证据，不能用补跑绿色消除这两次完整回归的失败留痕。临时提交快照已删除，原工作区及未提交trace/search-router均保留。Ruff/format和暂存diff检查通过。

## 2026-10-09：物理删除、恢复与墓碑复核

基线8cfee6c。Research工作流与Agent Prompt调优仍暂停；只推进T049外围生命周期。先加正式HttpRuntime DELETE验收，因缺少清理Worker得到503（1 failed in 2.34s），再接执行器，不把删除PG登记当成物理删除。第一轮邻接27通过、3失败是迁移计数仍为5；追加0006后更新为6，未降低资源隔离或删除断言。

新增 `application/knowledge_cleanup.py`、Repository组合 `knowledge_deletion.py`，不增加独立内存事实源/可写队列。HttpRuntime的TaskRunner持有并观察maintenance任务，扫描PG deleting与deleted库存；不启动全局Runner的CLI组合根不可接受删除。DELETE在短事务保存屏障及原202响应，重复同key重放原响应；新key对deleted返回200。缓存经过公有Schema、资源/父子/HTTP码/状态及Failure检查，不返回损坏缓存的私有字段。

wait_jobs给accepted/processing Job发取消信号，保留旧lease直到任务退出/到期；有旧租约时保存其最大到期时间+60秒静默窗口（无旧租约不等）。共用生命周期租约、心跳及revision/token/SQL时钟fence。索引清理是真Standalone drop_partition或逐version删除；对象仅清owner/KB或owner/KB/version前缀，MinIO每前缀30秒上限、取消停止下一SDK请求、原生请求未退出前不释放I/O容量，并重新列举核验缺失。index/objects完成才提交下一cursor；恢复时重复核查已完成索引/对象。依赖失败脱敏保存Failure，保持deleting/cursor和lease直到到期，不能与不确定的在飞行请求竞争。

最后事务移除对应PG Chunks、清active pointer，active Version改retired并保留历史chunk_count/activated_at、completed Job原样保留；未发布版本failed、未完成Job cancelled，保留全部资源和Job历史。最终SQL-clock CAS过期会回滚所有此前metadata写入。每60秒重新纳入deleted墓碑核验；每tick最多一个删除和一个墓碑，清迟到partition/向量/对象但不复活资源。私有cleanup_not_before/cleanup_verified_at不泄漏公有响应；成功GC无Failure时不变公有revision，GC故障/故障清除作为公有Failure变更递增revision。

独立暂存快照（仅HEAD+本批删除改动，不含未提交search-router/trace）使用原.venv、原Compose依赖执行：

```sh
python -m pytest -q tests/integration/test_mono_kb_delete.py tests/unit/test_content_cleanup.py tests/integration/test_mono_knowledge_cleanup.py tests/integration/test_mono_kb_http.py tests/integration/test_mono_knowledge_management.py tests/integration/test_mono_kb_lifecycle.py tests/integration/test_mono_ingestion.py tests/integration/test_mono_ingestion_http.py tests/integration/test_mono_migrations.py tests/integration/test_mono_document_content.py tests/integration/test_mono_milvus.py tests/unit/test_knowledge_models.py tests/unit/test_vector_models.py --tb=short
```

**187 passed in 58.09s**，包含新增21项PG/真实依赖测试及2项原生线程单测；此前开发阶段同目标180项通过、删除首批7项通过、SIGKILL/原生取消11项通过。不是有效PDF/MinerU/BGE或研究质量验收：metadata/原始字节/向量为明确受控fixture，PG/MinIO/Milvus真实。

覆盖KB与Document全部active/retired/staging版本源/解析/manifest/chunk/index删除；同KB其他Document与其他KB不受影响；research-content冻结摘录仍可读；completed Job完全相同、历史版本保留、未完成Job取消。删除前活Job不会提前物理清理，迟到activate拒绝；真实静默窗口只在隔离fixture中提前时间，避免90+60秒测试睡眠，不假称生产静默期已实际等待。真实TCP进程分别在index外部成功但cursor未提交、objects外部成功但metadata未提交时SIGSTOP→SIGKILL(-9)，新独立进程接管同身份/原幂等响应并完成清理。独立lease SQL时间提前只在test库；SIGKILL不是graceful shutdown替代。

删除后实际再建partition/写向量与MinIO对象，提前隔离墓碑复核时间，扫描再次物理清除；GC依赖故障维持deleted与诊断，重试清除Failure不复活。测试另验证持续续租的排他、失租取消停止下一I/O、metadata事务内pg_sleep使租约过期后整个发布回滚、错误owner/父子/key/body无副作用、私有/损坏幂等缓存返回503。

阶段中完整工作区回归 **1231 passed, 2 failed in 341.44s**（包含既有未提交search-router/trace；最终GC故障revision细化和追加GC用例随后由上述独立187项验证，因此不宣称这是最终提交全量）。失败是 `test_mono_run_tcp.py::test_probe_cli_creates_answers_then_requires_explicit_approval` 的20秒观察与 `test_tui_live_http.py::test_tui_default_workers_clarify_confirm_sse_and_report` 的25秒观察。未改deadline/Prompt/业务输出以换取通过。前者失败时诊断：Session ready/revision3；Run `717607f0-d1a9-42bb-99c5-919fe406a680` ready/phase plan/checkpoint_seq1/attempt_count0/lease_token0，只有初始checkpoint，无ToolAttempt，PG连接3个idle/ClientRead。这只能说明当时已冻结Brief但Run尚未被领取，不能证明具体卡点。TUI该用例尚无同等失败快照，根因仍待查；T056不勾选。

只复用五个已有healthy Compose服务；fixture清理本轮独立PG数据库/bucket/UUID partition，无新测试容器或用户数据卷操作。未修改用户.env、Agent Prompt、共享ResearchBrief/PipelineState、报告结构或docs/implementation。仅T049验收，不把T040/T042/T047/T048/T052/T055的完整入库/检索/真实模型/部署闭环提前勾选。

原HTTP/TUI文件组合补跑 `pytest -q tests/integration/test_mono_run_tcp.py tests/integration/test_tui_live_http.py --tb=short`：**5 passed, 1 failed in 57.87s**。本次原CLI与TUI两项通过，另一个SSE15秒用例超时；PG诊断却显示Run `5fb6a7b0-a337-4032-88c4-91d772fb7c22` 已completed/done/seq20，1次LLM ToolAttempt成功。创建于01:53:51.158878 UTC，首次领取01:54:01.212766，结束01:54:01.699965；没有失败或expired lease。直接证据将本次问题缩小到领取延迟/终态观察窗口，而非材料/Prompt失败；为什么约10秒后才领取、为何终态未在该窗口被观察到仍未确定，不把补跑叫作修复。删除提交依据是独立最终快照187项，不改变这些HTTP/SSE门禁。
