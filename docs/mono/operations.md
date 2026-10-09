# DR4A 后端运行与失败语义

> `mono-v1` 目标基线。此处规定实现必须具备的故障与运行行为，不表示现有环境已经验收。字段见 [data-model](data-model.md)，事务步骤见 [dataflow](dataflow.md)，外部响应见 [API](api-contract.md)。

## 1. 失败、降级与完成

`Failure = {code,dependency:string|null,operation,phase:ResearchPhase|null,message,retryable:bool,resume_allowed:bool,attempt:int,occurred_at,details:object|null}`。消息脱敏；HTTP/SSE 使用公共子集。状态中的失败与 errors 是已提交证据，不能从没有持久化的日志推断任务成功。

`Degradation = {source,reason,operation,section_id:string|null,occurred_at}`。查询真正无结果记录 Gap，不记录 dependency_unavailable。依赖失败不能让底层检索返回 []；Research 在可选来源失败时可显式消费错误、记录降级/缺口继续。

| 依赖/情况 | 有界动作与结果 |
|---|---|
| LLM 网络/超时/429/5xx | 总最多3次调用（初次+2次重试），耗尽当前操作失败；Clarify旧状态保留，Run失败可恢复 |
| LLM 非法 JSON/Schema | 最多1次带校验错误的修复调用，包含在3次总上限；禁止空默认变成功 |
| 搜索单源超时/不可用 | 总2次（初次+1）；可选来源跳过并记录 Gap；所有关键要求都不可取证仍可写不足报告，但不虚构结论 |
| 正文不可得/版本不明 | Gap；摘要只用于候选发现，不能填原文引用 |
| Embedding/Milvus/MinIO 在线不可用 | KB接口503；Research按是否可选降级；public-only不能转向私有外部服务 |
| Reranker故障 | 显式允许时融合顺序+degraded，否则503；正文不可读不能这种降级 |
| Parser无内容/坏PDF/超限制 | Job.failed，明确 parse_empty/invalid_document/resource_limit；空chunk不是completed |
| 沙箱超时/失败 | Artifact.failed；非必需计算可披露限制；必须计算无法执行则needs_more_work或Run失败，不编数值 |
| PG暂不可用 | 停止新工具调用和状态推进；按通用退避最多3次重连；未提交结果不可发布成功；等待租约恢复扫描 |
| 请求/阶段Schema不合法 | 拒绝；输入422/CLI2，模型输出503或Run.failed；未知phase不默认为done |
| Deadline/预算耗尽 | 停止扩展，按预留预算收缩/审阅；仍不能交付则failed，保留Checkpoint |
| 取消 | 持久请求→安全停止→cancelled，不以TCP断开代替 |
| 达返工上限 | 限制收缩+复核；verdict可能needs_more_work，绝不直接approved |

review_verdict 与 completed 分离：completed 证明“按契约发布了辅助报告”；approved 才表示审核无关键问题。needs_more_work 报告须标未闭环主张，剔除假引用和不合格计算；缺固定章节、悬空ID、私有泄露等硬错误不能发布。

PG故障导致连failed都不能提交时，只发fatal error(persistence_unavailable)并关闭连接，不能发声称已持久失败的done。PG恢复后、租约仍有效则提交失败；失租则由扫描器把过期running置failed。GET不可用时返回503，不用内存伪造终态。扫描在启动及运行期间每5s执行，避免必须重启才识别过期任务。

## 2. 默认预算与超时

以下是 V1 可执行默认配置，不是性能承诺；写入 Run.config_snapshot，调优需容量实测。所有循环都有上限。单次操作超时+重试时间受 Run 总deadline裁剪，不能重试重新计时延长总任务。

| 配置 | 默认 |
|---|---|
| 后续自动Clarify轮次 | 3，初始round=0不计；到上限仍缺关键项保持ask |
| 问题数/轮 | 1–2；confirm=0 |
| Run总deadline | 1800s，包括等待外部调用与本Run执行；ready排队时间另记，恢复不重置已用执行时间 |
| 检索调用预算 | 每Run search=60、fetch=30；每ClaimSpec补查≤2，追溯深度≤2 |
| LLM预算 | 每Run总60次/输入输出累计120000 tokens；其中至少8次调用和12000 tokens留给限制收缩+最终审核 |
| 返工轮次 | 最多3次额外回流；终末收缩write+review最多1轮 |
| LLM / 单源Search / Fetch | 单尝试60s / 20s / 45s |
| Embedding批 / Rerank / Milvus / MinIO | 120s / 60s / 15s / 30s |
| Parser / Sandbox | 单文档600s / 单操作30s |
| Job执行deadline/最大尝试 | 每次1200s；最多3次领取（含手工重试和崩溃自动恢复） |
| 重试退避 | 1s、2s指数退避+0..0.5s jitter；Retry-After至多30s，受deadline裁剪 |
| 全局Pipeline / 每owner活跃Run | 2 / 1；ready排队最多20/owner，超限429 |
| 全局入库 / 每KB入库 | 2 / 1；每Document只允许1个活动Job |
| Search并发 / Fetch并发 / LLM并发 | 4 / 2 / 2（全进程信号量，源独立timeout） |
| 模型本地推理并发 / Embedding批量 | 1 / 16；强制有界线程池 |
| 候选召回 / 结果top_k | 每路20、RRF合并20；默认5，最大20 |
| 上传 / 页 / chunk | 50MiB / 500 / 10000；流式上限，不一次await file.read无限内存 |
| 文本chunk / 原子table/formula | text目标800 tokens，最大1200；原子块最大8000 tokens，超出失败并说明，不截掉Note |
| lease / heartbeat / scan | 90s / 20s / 5s；PG时间，heartbeat失败停止外部扩展 |
| SSE heartbeat / queue / event大小 | 15s / 每订阅者256 / progress≤8KiB；心跳无业务state |

来源硬要求与可选性由 Brief/SectionPlan 表达，例如指定本地独有实验数据且读取失败不能用Web替代。Agent申请的调用先由预算器事务预留；实际消耗/重试都计数，并发不能突破上限。停止原因保存RunMetadata与Gap。

HTTP每owner默认创建10次/分钟、messages/confirm 30次/分钟、KB搜索60次/分钟；登录按IP+email 10次/分钟。限流不持久改变Session。队列等待超过30分钟ready→failed(queue_timeout)，允许确认冻结内容不变地resume；排队不是run执行时间。

## 3. 幂等、事务与版本

### 3.1 变更请求

(owner,operation,key)唯一；operation包含资源ID与动作。先reserve并设120s操作租约；长同步模型操作续租。请求hash包含trim后的正文、版本、SourceSelection，multipart含文件hash。相同key不同body=409；处理中=409+Retry-After；completed重放原status/body至少7天。

完成资源变更与缓存成功响应同PG事务；409/422业务拒绝可缓存，临时503/超时不缓存为completed，释放reserve供重试。create KB在creating阶段已建立资源身份，重试沿用resource_id，不能再建；扫描恢复使active后才缓存201。上传PG建任务前失败的源对象有TTL，不成为正式文档。

确认事务锁Session、所选KB/Document版本范围，校验expected revision和brief_version，写冻结Brief、Run、初始Checkpoint、状态和幂等响应。Unique(session_id)防第二Run。最终交付同事务写Report、done Checkpoint、Run/Session.completed。任何事务失败回滚全部PG变更。

GET SessionView、最新Checkpoint与Report投影使用同一短只读REPEATABLE READ快照核对Session/Run/Brief/Checkpoint/Report的一致关系，不对Session取FOR UPDATE锁。读请求不能使SKIP LOCKED领取遗漏ready任务，也不能通过分次READ COMMITTED读拼出半个并发状态；快照内禁止写入，后续GET获得新的已提交快照。写事务的父子锁顺序、revision/token校验和原子提交保持不变，SSE持久轮询复用同一只读投影。

### 3.2 工具调用与“重复为0”的边界

call_key = hash(run_id,tool/provider/prompt或template版本,规范参数,输入内容hash,source/知识版本范围)，不含随机attempt/timestamp。持久reserve预算和调用身份；成功结果先存不可变对象，再写ToolCallRecord.succeeded；合并State后Checkpoint记录unit_manifest。

恢复遇到succeeded直接读取结果；已经提交的章节/查询/草稿不重新执行。不完整单元使用相同ID重试。staging写入用upsert，Claim/Evidence/Link按身份集合并。

外部服务若在“收到请求→回本地保存结果”之间崩溃，无法承诺物理调用恰好一次：将reserved判uncertain，优先查上游幂等/已落对象；不支持时可在预算内重发**只读**搜索/模型调用，记uncertain_replay。费用可能重复，事实/成果不得重复。旧SC-006“重复查询绝对为0”收敛为：已持久成功调用不重发、写入不重复；不把分布式不确定窗口宣称已解决。此限制属于验收结果必须披露的边界。

### 3.3 版本与恢复一致性

Checkpoint(run,seq)单调；更新需expected_seq和lease_token。旧token写入拒绝，不能让迟到Worker覆盖新尝试。State hash/schema/brief_hash与Run一致；恢复配置默认使用原配置，模型/prompt/profile不可静默换版。目标配置不可用或schema无迁移则failed(schema_incompatible/config_unavailable)、resume_allowed=false；人工建立迁移或新Session。

派生产物基于输入hash；返工只让目标派生失效，旧快照不可改。reviewed_draft_version必须等于交付draft_version；不能把审核旧稿结论用于新稿。未改章节的绑定也保留并更新全局版本。

## 4. 租约、取消与恢复

### 4.1 任务执行

领取ready/accepted采用PG行锁或条件UPDATE，写lease_owner=进程UUID、token+1、expires_at，attempt_count+1。每20s续租，单次大Parser调用由独立heartbeat协程维持，不持PG事务长锁。状态提交检token且expires_at未过期；失租即停止新I/O，丢弃不可提交结果。

每个服务器/CLI保留任务强引用并观察异常，finally释放引用/租约；不能create_task后无人读取exception。SSE生命周期与任务分开。开发同时运行服务器和CLI仍使用租约/PG并发约束；服务容量计数在领取事务内，不只内存Semaphore。

### 4.2 启动扫描与shutdown

- ready Run继续排队；过期running改failed(interrupted)，resume_allowed按快照/配置判断；不自动重跑研究让用户不知情地产生费用。
- cancelling Run完成停止并cancelled；没有活租约时可由恢复器提交取消。
- accepted Job继续排队；过期processing若manifest/profile有效且attempt<3则accepted，version继续staging；否则failed。自动恢复同Job，不重建ID。
- creating KB幂等ensure partition，成功active；deleting KB/Document继续cursor清理，各自领取清理租约。
- shutdown停止接收新变更/领取；最多30s等安全Checkpoint，保留PG取消请求；无法安全完成的尝试不写completed，由租约过期扫描处理。SIGKILL测试必须覆盖，无需假设graceful总成功。

### 4.3 取消边界

接受取消写PG，执行器在每query/fetch/LLM/批次/章节/分析之后检查；最长响应延迟受单操作timeout（Parser最长600s，可用子进程实现更快终止）约束。不能仅检查phase边界导致整章串行检索长时间不停止。

Run取消保留已提交成果与快照，无Report。Job取消停止新写入、清staging索引与派生对象，保留原始源30天与长期版本/Job元数据；状态cancelling直到清理完成。

同内容再次submit仍返回已有cancelled Job，不隐式复活。用户可显式retry一个已完成清理的cancelled Job，前提是源对象仍存在、资源active且attempt_count<3。retry清cancel_requested_at、重置派生manifest/progress，将version置staging、Job置accepted；原取消记录保留attempt_history。超过30天源被回收或次数耗尽时retry_allowed=false；重新处理需新的已批准ingestion_version或管理迁移，不能让普通重复上传绕开身份约束。

## 5. 知识库一致性与检索质量

### 5.1 完整入库

源文件存MinIO→PG任务accepted→结构化解析/切片→正文和manifest→Embedding→Milvusupsert/强一致可读→PG事务发布Chunk映射、新active版本、旧retired与Job.completed。新版本不可见直到最后一步。index写成但PG失败是staging；恢复复用完整manifest与批次，不提前可见。

入库失败version.failed；旧active不动。重试相同Job/version/chunk ID，清理不属于manifest的多余索引。所有幂等身份约束在PG，外部不同hash同key不得覆盖。文档替换与删除锁同Document；KB删除先屏障，等待持租任务停止，再清索引/对象，PG墓碑最后写。

creating 与 deleting 使用同一 KB 租约，创建完成前再次检验 status/revision/token，不能把 deleting 改回 active。旧 Worker 的外部写入无法由 PG token 直接阻断：失租停止后续 I/O；删除等待已发起操作结束或其超时，再清理。若进程失联或上游迟到写入，PG 删除屏障始终阻止可见性，周期清理器对 deleted 墓碑范围重复核对并清除迟到 partition/对象/向量；不能承诺跨存储瞬间物理原子删除。

HTTP DELETE 与幂等响应在同一短事务提交屏障，返回202；物理清理由 TaskRunner 的持有任务扫描 PG 库存，不由 HTTP 请求携带 SDK 操作。清理顺序为 wait_jobs → index → objects → metadata；旧 Job 收到取消信号，活租约和 `cleanup_not_before` 未结束时不做外部删除。默认静默窗口60秒；无旧租约的资源无需等待。清理租约90秒、续租20秒，外部依赖失败保留当前游标和租约直至到期，避免与仍在飞行的 native 请求竞争。MinIO 每前缀清理限时30秒，取消后停止发起下一 SDK 请求，并在成功前重新列举核验空前缀；恢复时重复核验已清索引和对象。默认每60秒将 deleted 墓碑重新纳入扫描，每 tick 至多一个删除资源和一个墓碑；只清授权 KB/version 前缀与 Milvus partition/version，不清报告或 research-content。配置了不启动全局 Runner 的 CLI 组合根不能接受资源 DELETE。

退役版本保留直到未被活动Run引用；Research冻结版本供本Run复用，publicsearch仅取当前active。删除明确覆盖所有版本，执行中Research遇删来源写Gap；已经保存的引用摘录/Report不随KB删除消失。附件和内容缓存的保留须跟成果策略一致。

### 5.2 模型与索引固定

BGE-M3使用真正支持dense/sparse输出的FlagEmbedding实现，不能把SentenceTransformer通用encode当已支持特定参数。启动验证1024维dense、int token ID sparse、有限值、非空真实样本；失败readiness降级且相关操作503。model revision/tokenizer/chunker版本进入ingestion_version。

V1 learned sparse不是BM25。Milvus2.6目标schema固定：dense用COSINE、sparse用IP，适配器以双ANN请求+RRF(k=60)融合；排序得分不当概率。collection/partition命名在data-model，所有读写/删除/doctor同映射。服务与客户端版本真实兼容测试后锁定，不升级依赖就声称可用。

候选先PG可见性与授权过滤，取正文再rerank。若部分索引没可读或Schema错明确index_not_ready；metadata以PG为准，不信Milvus去规范化字段做授权。表格/公式完整保存；网页抓取保留时间/hash/定位。论文候选返回paper不能自动证明同行评议。

### 5.3 清理与备份

PG为状态/元数据事实，MinIO为内容事实，两者都备份；Milvus可从内容/manifest/profile重建。备份采用同一维护窗口或版本manifest记录边界，restore后先校验引用对象与hash，再重建索引、恢复readiness；只恢复PG而无正文不叫完整恢复。

临时上传/孤儿对象TTL24h；失败/取消staging诊断保留30天；已发布报告/快照/引用保留直到显式成果清理政策，不自动TTL。垃圾收集以PG引用集合与任务租约校验，不能凭文件修改时间删除仍用对象。KB/document物理内容删除完成后保留墓碑/任务记录，避免无法定位先前删除失败。

## 6. 安全与隐私

正式环境JWT_SECRET必须随机足够长、禁止空值或change-me；JWT.exp=1h，algorithm固定；Argon2id参数采用库推荐安全配置并以部署容量验证。日志不存密码/token/API key，也不默认输出研究原文；调试verbose仍遵章程。

KB默认private；public必须用户声明为可公开资料。本地Parser/Embedding/Rerank/MinIO/Milvus不出网。private内容、摘录、由其生成的查询不发送外部LLM或搜索服务；选private KB时必须配置本地LLM且来源仅knowledge_base，否则privacy_policy_conflict，确认前拒绝。本地报告也不经外部模型审核。公开query接口只供可公开研究请求；界面/CLI说明不能提交未发表课题私密信息，不能声称服务可自动判断所有敏感数据。

Fetch防SSRF：只http/https，DNS解析后拒绝localhost、私网、metadata地址和非公网IP；每次重定向重新检验，最多3次，下载最大50MiB；输入URL只是资料，不可当执行指令。PDF/网页/模型输出均是不可信数据，prompt隔离；模型不能指定额外网络目的地、KB ID、文件路径或任意模板。

分析用固定模板和不可变输入；开发Docker执行器无network、read-only root、non-root、CPU1/memory256MiB/pids64、cap-drop/no-new-privileges，输入/脚本ro，输出仅专用目录。输出hash、大小≤10MiB、媒体类型和路径检查；超时停止**容器**而不只杀docker CLI。生产无Docker socket；必须提供同限制的本地隔离Worker（如部署配置的nsjail），不能静默退回exec()。其安装/模板镜像/脚本挂载与结果目录权限需真实沙箱验收；当前Docker Adapter漏挂脚本，现有Compose注释不证明nsjail可用。

## 7. 部署、健康与观测

开发默认端口保持已有约定：PG5432、MinIO9000/console9001、Milvus19530/health9091、API8000。宿主backend使用localhost，容器用postgres/minio/milvus服务名；应用MinIO与Milvus内部minio-milvus分开，不能把应用原文写进Milvus内部bucket。

从仓库根起infra：
```bash
docker compose --env-file backend/.env up -d
```
当前代码启动命令（在backend，路径迁移前）：
```bash
uv sync --extra dev
uv run uvicorn interface.main:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
uv run python -m cli doctor --json
```
生产Compose精确使用同env作插值来源：
```bash
docker compose --env-file backend/.env -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
这些命令描述现有入口，不能证明目标能力上线。实施时必须补Parser/FlagEmbedding/Argon2/MinIO等依赖锁、模型两套权重挂载、隔离Worker、迁移与配置；禁止在运行时无提示下载不固定模型revision。

配置优先级：显式CLI配置 > 进程环境 > backend/.env > 非敏感默认；HTTP入口与CLI共Settings。保留DATABASE_URL/MILVUS_URI/MINIO_ENDPOINT及凭据/BGE路径/LLM_MODEL/ANTHROPIC_BASE_URL/BOCHA_API_KEY；新增DR4A_ENV、LLM_LOCAL、RUN_*预算、PARSER_*、SANDBOX_*、CORS_ALLOW_ORIGINS。启动校验敏感必需值；run配置保存非密钥值，凭据只保存在进程环境。

GET /health是liveness：200 {status:"ok",api_version:"mono-v1"}，进程能响应，不调用收费API；GET /ready为readiness：200或503 {status:"ready"|"degraded",checks:[{name,status:"pass"|"fail",message}]}，检查PGschema、应用MinIO读写小对象、Milvusschema、模型/Parser/执行器本地能力，不做真实研究。缺可选源可degraded仍200并列明；PG不可用或宣称必备的功能不可用503。生产流量以ready为门。TUI /connect健康检查只证明可连接。

结构化日志：request/session/run/job/unit IDs、attempt、lease_token、phase、seq、draft_version、duration、dependency、failure.code、预算；日志为诊断不是事实源。metrics包括队列长度、phase/query耗时、错误/重试/降级、lease失效、取消延迟、未补Gap、引用完整性、Job状态与chunk数。事件不能含密钥或私有原文。phase边界之外有query_started/query_completed/section_completed，便于定位真实research卡点。

## 8. 实施与设计验收清单

文档验收：五份枚举/字段/方法可互相追溯，示例符合Schema，所有变更有成功/失败/并发/恢复结果；旧文档权威入口明确转向本基线。下面是**后续代码验收**，不是本次写文档已通过的实现测试。

| 编号 | 要证明的行为 | 必需证据 |
|---|---|---|
| A1 | 初始不足ask，充分confirm；1–2问题；最多3轮不自动冻结；三task与拒绝reviewer | HTTP契约测试+Machine单测；ten-field校验、limit场景 |
| A2 | 用户确认版本，重复确认仅1Run；旧版/并发message拒绝；归属有效 | PG真实事务并发测试；跨用户404；非NULL owner |
| A3 | 冻结、Run、Checkpoint原子；报告/终态原子 | 事务故障注入，各写入点中断，SQL核对；没有半冻结/空完成 |
| A4 | 返工正确目标、保留旧绑定、审核同版、上限不假通过 | 定向research/analyze/write/review fixtures；完整snapshot链 |
| A5 | 不同条件/缺条件不compatible；Artifact回链；模板限制 | 两dataset/协议/指标及null反例；真实沙箱计算与超时 |
| A6 | Report固定0–5+References、专属payload、所有事实引用、不足有Risk | 三任务端到端报告与cases结构核对，逐条来源抽查；非逐字diff |
| A7 | 一个真实PDF→Parser→MinIO→dense+sparse→检索正文→Research引用 | 完全real的文档/job/chunk/version IDs、对象hash、Milvus查读、Report回链；不以fakeParser替代 |
| A8 | staging不可见；替换失败旧active仍可检索；重试去重；完整删除 | PG/MinIO/Milvus真实故障注入/状态查询；completed历史retired仍成立 |
| A9 | SIGKILL恢复最新seq而非最高phase；缓存成功调用不重发；预算保留 | 返工中的中断、工具结果保存后中断、确认返回后spawn前中断；调用记录 |
| A10 | SSE迟到/多订阅/断线/失败终态，不启动任务；取消持久且竞争确定 | HTTP/SSE真实时序测试与重启，PG终态核对 |
| A11 | 正式auth/cookie过期、CSRF、profile隔离、private不出网 | 认证/安全测试；mock外发计数为0、实际日志脱敏 |
| A12 | CLI单phase复用、fake确定、real副作用/doctor明确 | CLI subprocess测试；run/dump同一run/seq核对 |
| A13 | 备份恢复、模型锁定、生产沙箱与readiness | 清洁环境部署记录、备份恢复演练、权限/资源限制实测 |

旧124通过等历史记录只证明当时测试，不能作为A1–A13的证据。设计基线完成后，CodingAgent应按该表建立实现任务和证据，不把文档中的目标语句写成测试已通过。
