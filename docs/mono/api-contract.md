# DR4A 后端 API 契约

> `mono-v1` 目标基线。Web/pi-tui 用公开 HTTP/SSE；CLI 调同一 App 用例。类型语义来自 [data-model](data-model.md)，状态前置来自 [dataflow](dataflow.md)，失败与预算来自 [operations](operations.md)。本文件替代旧 Research API 传输草案。

## 1. 通用 HTTP 规则

V1 无路径前缀；JSON UTF-8，Content-Type=application/json。未知请求字段、非法 UUID/enum、空必填字符串、超长参数返回 422；所有受保护路由先取得 owner_id，Service 仍校验归属。客户端可忽略新增可选响应字段，不能猜测未知状态。以下带 `?` 是可选请求字段；响应中可空字段实际输出 null。成功状态码必须显式设置，不使用 FastAPI 默认 200 代替 201/202。

每次响应带 X-Request-ID；所有 POST/PATCH/DELETE 资源变更（认证除外）必须带 `Idempotency-Key: <1..128 字符>`。重复同 owner/operation/key+同规范请求返回原已提交响应；不同请求返回 409 idempotency_conflict；处理中返回 409 request_in_progress 和 Retry-After。幂等重放响应可较实时状态旧；事实用 GET。GET 无幂等要求。multipart 请求 hash 使用文件 SHA-256+参数。

公共失败只使用以下形状，包括 FastAPI 请求校验错误：

```json
{
  "error": {
    "code": "stale_brief",
    "message": "任务书已更新，请读取当前版本后确认。",
    "request_id": "95a50b7b-79c0-4b91-81e0-55e6dd283dcb",
    "retryable": false,
    "details": {"current_brief_version": 3}
  }
}
```

details 是安全的结构化诊断，可空，不含堆栈/密钥/数据库地址。HTTP error.retryable 指该请求可重试；Failure.resume_allowed 指运行可恢复，两者不同。

| HTTP | code 与语义 |
|---|---|
| 400 | malformed_json（不能解析 JSON） |
| 401 | unauthenticated、invalid_credentials |
| 404 | session_not_found、knowledge_base_not_found、document_not_found、job_not_found（包含非 owner） |
| 409 | invalid_session_state、stale_brief、stale_resource、report_not_ready、idempotency_conflict、request_in_progress、document_busy、content_identity_conflict、resource_not_active、resume_not_allowed、privacy_policy_conflict、email_already_registered、name_already_exists |
| 413 / 415 | file_too_large / unsupported_media_type |
| 422 | validation_error、unsupported_task_type、invalid_filter、invalid_brief、invalid_state |
| 429 | rate_limited（Retry-After；速率/接收容量限制，不代表自动冻结） |
| 503 | dependency_unavailable、model_output_invalid、content_unavailable、index_not_ready、service_not_ready |
| 500 | internal_error |

### 1.1 认证与开发 profile

正式模式从 Authorization: Bearer JWT 或 access_token cookie 取得身份；两者同时存在但身份冲突返回 401。禁止 URL query token。JWT 校验签名、算法、sub、iss、aud、exp；过期 401。owner_id 从认证依赖传入 Service，客户端不提供 user_id。

开发 profile：`DR4A_ENV=development` 且 `DR4A_AUTH_REQUIRED=false` 时使用固定开发用户 UUID `00000000-0000-4000-8000-000000000001`，先写 users。正式部署 DR4A_ENV=production 必须 auth=true，否则启动失败。匿名只改变身份获取，Schema/所有权/状态机相同；当前 TUI 不带登录凭证。

### 1.2 认证端点

- POST /auth/register body={email:string,password:string}；email 3–254；password 8–128 Unicode 字符。201 {user_id:UUID}；重邮箱 409 email_already_registered。使用 Argon2id 保存 hash（选择理由是避免现有 bcrypt 字节上限与 128 字符契约冲突），不返回 hash。
- POST /auth/login 同 body；200 {access_token:string,token_type:"bearer",expires_in:3600}，同时 Set-Cookie access_token、HttpOnly、SameSite=Lax、Path=/、Max-Age=3600；HTTPS 正式模式 Secure=true。错误统一 invalid_credentials，不泄漏是否已注册。
- POST /auth/logout body={}；204，清 cookie。V1 JWT 不做即时全局撤销，已签发 Bearer 直到 exp 仍有效，客户端须丢弃 token；文档不称其“全局登出”。
- cookie 变更请求检查 Origin/Referer 与配置允许同源；Bearer 程序客户端不依赖 cookie，跨源 Web 必须 CORS 具体 allowlist+credentials，不用通配。SSE 原生 EventSource 使用 cookie；fetch SSE 可用 Bearer。401 必须在打开 SSE headers 前返回。

## 2. Research 公共契约

### 2.1 POST /research

创建资源并同步执行初始 Clarify，不启动 Pipeline。

```json
{
  "query": "比较 Transformer 与 CNN 在公开入侵检测数据上的机制差异",
  "task_type": "method_differentiation",
  "sources": ["papers", "web"],
  "knowledge_base_ids": []
}
```

query 必填 1–16000；task_type 可省略，存在必须为 V1 支持值；sources 可省略（papers+web），仅 SourceCategory；knowledge_base_ids 可省略（[]）。knowledge_base 被选中时 IDs 必填非空（最多 10），否则必须为空；类别/KB 一一授权。sources 最终锁定于用户确认。

201 响应是判别联合，两个分支都含 session_id、status、brief_version、clarification_round、clarification_limit_reached、source_selection：

```json
{
  "session_id": "d7d25f5b-fd67-4ef6-b9d3-bcd34acb8a77",
  "status": "ask",
  "brief_version": 1,
  "clarification_round": 0,
  "clarification_limit_reached": false,
  "source_selection": {"categories": ["papers", "web"], "knowledge_base_ids": []},
  "questions": ["比较针对哪个数据集和研究决策？"],
  "missing_fields": ["scope", "decision_goal"],
  "brief_draft": {"task_type": "method_differentiation", "research_object": "入侵检测技术路线"}
}
```

confirm 分支去掉 questions/missing_fields/brief_draft，增加 research_brief，必须是完整十字段；不会返回 sse_url。精确完整示例：

```json
{
  "session_id": "d7d25f5b-fd67-4ef6-b9d3-bcd34acb8a77",
  "status": "confirm",
  "brief_version": 2,
  "clarification_round": 1,
  "clarification_limit_reached": false,
  "source_selection": {"categories": ["papers", "web"], "knowledge_base_ids": []},
  "research_brief": {
    "task_type": "method_differentiation",
    "decision_goal": "判断两条路线是否存在可验证的机制差异",
    "research_object": "Transformer 和 CNN 的入侵检测方法",
    "scope": "公开数据、有限算力；不评价生产环境效果",
    "comparison_scope": "输入表示、建模机制、输出与评测条件",
    "claims_to_verify": "机制差异及其验证条件",
    "evidence_requirements": "原始论文、实验设置和官方代码",
    "conclusion_boundary": "不在不同数据切分下直接排名",
    "deliverable": "方法比较矩阵、差分主张与风险清单",
    "assumptions": "算力按单机单卡保守规划"
  }
}
```

示例 confirm 是后续轮响应，也可作为初始 201 分支（version=1、round=0）。201 表示资源创建，不表示研究完成。

### 2.2 POST /research/{session_id}/messages

只在 ask，body={content:string,brief_version:int,brief_patch?:Partial ResearchBrief,source_selection?:SourceSelection}。content 1–16000。version 必须匹配当前；补充十字段不包含控制字段。200 AskResponse 或 ConfirmResponse（同上，session_id 始终存在）。

到澄清轮数上限后保持 ask，客户端提交明确 brief_patch 补齐任务书；Service 确定性校验，不强制再调用模型。关键字段不足仍 ask；每次成功请求增加 version/round，重放不增加。该接口不能当聊天接口，在 ready/running 等状态返回 409。

### 2.3 POST /research/{session_id}/confirm

- 接受：{accepted:true,brief_version:int}。不得附 feedback 或改 Brief。
- 退回：{accepted:false,brief_version:int,feedback:string,source_selection?:SourceSelection}，feedback 1–16000；在 confirm 时处理反馈，200 AskResponse，新版本与轮次。不能直接 accepted=false 后启动 Pipeline。
- 成功接受：202，代表已冻结并持久接受 Run，可能尚未领取执行：

```json
{
  "session_id": "d7d25f5b-fd67-4ef6-b9d3-bcd34acb8a77",
  "run_id": "25d5eeb2-7dfd-4ef5-ac39-00f145a3cbdc",
  "status": "ready",
  "brief_version": 2,
  "sse_url": "/research/d7d25f5b-fd67-4ef6-b9d3-bcd34acb8a77/events"
}
```

非 confirm、旧 version 为 409。相同已冻结 version 的再次 accepted=true 返回既有 202 Run（即使使用新幂等键）；确认请求自身不依赖再一次 LLM。sources 中 KB 已删除或模型不能处理私有内容时 409，事务不冻结。

### 2.4 GET /research/{session_id}

200 SessionView 始终包含：

```json
{
  "session_id": "d7d25f5b-fd67-4ef6-b9d3-bcd34acb8a77",
  "status": "running",
  "phase": "research",
  "revision": 6,
  "brief_version": 2,
  "clarification_round": 1,
  "clarification_limit_reached": false,
  "source_selection": {"categories": ["papers", "web"], "knowledge_base_ids": []},
  "run_id": "25d5eeb2-7dfd-4ef5-ac39-00f145a3cbdc",
  "checkpoint_seq": 4,
  "resume_allowed": false,
  "failure": null,
  "review_verdict": null,
  "sse_url": "/research/d7d25f5b-fd67-4ef6-b9d3-bcd34acb8a77/events"
}
```

ask 时附 questions/missing_fields/brief_draft，confirm 时附完整 research_brief，ready 及之后附冻结 research_brief。run_id/phase/checkpoint_seq/sse_url 冻结前为 null。completed 时 phase=done、review_verdict 非空；failed/cancelled 保留最后 phase 而不称完成。状态视图足够让断线客户端继续，无需历史 SSE。

### 2.5 GET /research/{session_id}/report

200 {session_id,report_id,version,review_verdict,report:string,references:Reference[],risks:RiskItem[]}。report 是固定骨架 Markdown，JSON 解码后为真实换行，不是重复转义字符串。所有者不可见/不存在 404；没有发布报告（含失败、取消）409 report_not_ready。只读默认最新发布版本，V1 每 Run 一个。

报告附件：GET /research/{session_id}/artifacts/{artifact_id}/files/{file_name}，只有当前 owner、存在于 Checkpoint/Report 的 Artifact 及其白名单 basename 可读；返回正确媒体类型与 Content-Disposition attachment。不存在/非法归属 404，内容暂不可读 503。拒绝任意 object_key/路径；无公开 MinIO URL 泄漏。

### 2.6 取消与恢复

POST /research/{session_id}/cancel body={}。ask/confirm/failed → 200 {session_id,status:"cancelled"}；ready/running/cancelling → 202 {session_id,status:"cancelling"}；cancelled 重复 → 200；completed → 409 invalid_session_state。接受取消不等于已停止。

POST /research/{session_id}/resume body={checkpoint_seq:int}。仅 failed、resume_allowed=true、seq 为最新，无取消、配置兼容。202 与接受确认的 Run 响应相同，保留 run_id；不新建 session/brief，不清预算。旧 seq 返回 stale_resource，不可恢复返回 resume_not_allowed。已 ready/running 的并发恢复返回 409（相同请求键重放除外）。

## 3. SSE 契约

GET /research/{session_id}/events，Accept:text/event-stream。只有冻结后允许；前置失败先返回 JSON。200 headers：Content-Type:text/event-stream、Cache-Control:no-cache、X-Accel-Buffering:no；每 15s 注释心跳，无缓存、不保证历史 replay，忽略 Last-Event-ID 且不声称恢复。

```text
id: 394747cc-e3a5-4cf8-a4df-713e6db53669
event: phase
data: {"session_id":"d7d25f5b-fd67-4ef6-b9d3-bcd34acb8a77","run_id":"25d5eeb2-7dfd-4ef5-ac39-00f145a3cbdc","timestamp":"2026-10-05T03:00:00Z","checkpoint_seq":4,"phase":"research","status":"running","message":"开始检索"}

```

每帧一个完整 JSON，共同字段 session_id/run_id/timestamp/checkpoint_seq；id 为事件 UUID，只供客户端本次去重，不是可恢复日志序号。所有事件可按 seq 同值出现，不能把同 seq 所有 progress 去重掉。

| event | 必需 payload（加共同字段） |
|---|---|
| phase | phase、status、message；持久提交后发布 |
| progress | phase、section_id（可空）、stage、unit_id、completed_units、total_units（可空）、message；可选 results/chart（摘要 ≤8KiB，不输出私有原文） |
| rework | issue_ids、action、target（phase）、section_ids、draft_version、message |
| error | code、message、recoverable:bool、fatal:bool；recoverable 表示可重试/恢复，不表示已恢复 |
| done | status:completed/failed/cancelled、review_verdict（可空）、final_report_url（只有 completed 非空）、failure（可空） |

stage 闭集：query_started/query_completed/source_degraded/section_completed/analysis_completed/draft_completed/review_completed。失败在终态提交后发送 done.failed（这是旧契约补齐）；客户端必须按 status 决定是否取报告，不能见 done 就当成功。非致命 error 后可继续；fatal error 后终态以 GET 或 done 为准。PG不可用而无法提交失败时只有fatal error并断开，恢复后查询事实，不能发假done。

订阅先注册独立队列再读持久状态；迟到/终态订阅会获得当前 phase 或 done，避免漏掉秒级完成。服务器从 PG 生成 bootstrap 投影，不能播放不存在的历史。队列每订阅者 256 条，慢消费者断开并通过 GET 重建。JWT 到期关闭 SSE，客户端重新认证；断开不会取消研究。

CLI 持租执行的 Run 不共享服务器的内存 EventBus。服务器每 5s 轮询所订阅 Run 的持久投影，seq/status 变化时补发当前 phase/done；可能合并中间阶段，不能承诺全量事件。CLI 本地 query 级 progress 只在其 stderr/结果 events 中提供；HTTP 启动、服务器执行的 Run 才有实时细粒度进度。两入口共享成果和生命周期事实，不假称共享跨进程瞬态队列。

## 4. 知识库 HTTP 契约

### 4.1 管理与分页

所有列表用 limit（默认20，1..100）、cursor（opaque，以 created_at+ID 稳定排序，不包含客户端可执行 SQL）；200 {items:Entity[],next_cursor:string|null}。cursor 与 owner/过滤绑定，非法 422。

| 路径 | 请求 | 成功响应及前置 |
|---|---|---|
| POST /knowledge-bases | {name,description?,data_classification?} | 201 KnowledgeBase，status=active；默认 private |
| GET /knowledge-bases | 分页，status? | 200 列表；默认不列 deleted |
| GET /knowledge-bases/{kb_id} | 无 | 200 KnowledgeBase，删除墓碑仍可读 |
| PATCH /knowledge-bases/{kb_id} | {revision,name?,description?}，至少一变更 | 200 更新 Entity；active，旧 revision=409 |
| DELETE /knowledge-bases/{kb_id} | 无 body | 202 {kb_id,status:"deleting"}；重复 deleting 202、deleted 200；creating 可删 |
| GET /knowledge-bases/{kb_id}/documents | 分页 | 200 Document 列表 |
| GET /knowledge-bases/{kb_id}/documents/{document_id} | 无 | 200 {document:Document,versions:DocumentVersion[],latest_job:IngestionJob|null}，版本列表最多100，更多用下方端点 |
| GET /knowledge-bases/{kb_id}/documents/{document_id}/versions | 分页 | 200 DocumentVersion 列表 |
| DELETE /knowledge-bases/{kb_id}/documents/{document_id} | 无 body | 202 {document_id,status:"deleting"}；重复等同 KB 删除 |

name 同 owner 重复 409 name_already_exists；data_classification 创建后不可由 PATCH 放宽，防止私有资料误改公开。entities 返回数据模型定义的公共字段（ID、展示属性、status、revision、进度、failure、UTC timestamps），不返回 lease_owner/token、MinIO key、密码等内部字段。DocumentVersion 公共视图只含 ID/归属、content_hash、ingestion_version/index_version、chunk_count/status/timestamps。

### 4.2 入库任务

POST /knowledge-bases/{kb_id}/documents，multipart/form-data：file 必填 PDF；document_id? 指已有 Document 的新版本；不指定创建新 Document。使用 Idempotency-Key；版本 profile 服务端固定，客户端不能提交任意模型/SDK 参数。限制 50MiB、500 页、10000 chunks；媒体类型/文件头校验，不能只信扩展名。

202 {kb_id,document_id,document_version_id,job_id,status:"accepted",job_url:"/ingestion-jobs/{job_id}"}。重复请求/同内容返回原身份与当前 Job 状态（幂等键重放原响应；内容去重非重放返回当前状态）。已 active 同内容也是 202 的已有 Job，不再取证入库。KB/Document 非 active=409。

- GET /ingestion-jobs/{job_id} → 200 JobView，含 IDs、status、attempt_count、progress、failure、retry_allowed、cancel_requested_at、timestamps。
- POST /ingestion-jobs/{job_id}/retry body={} → 202 {job_id,status:"accepted"}；仅可恢复 failed 或清理完成 cancelled，retry_allowed=true、资源 active、源文件仍存在、attempt_count<3；否则409 resource_not_active 或 resume_not_allowed。
- POST /ingestion-jobs/{job_id}/cancel body={} → 202 {job_id,status:"cancelling"}；failed/cancelled 返回200 cancelled；completed 返回409。清理失败仍 cancelling、failure 明示，不能提前宣称停完。

JobView 不含 lease 与 storage key。progress.total_units 可未知；不得以100%代替 completed。failure 使用 Failure 公共子集。直接入库用例不走 Research SSE，通过 Job GET 轮询。

### 4.3 POST /knowledge-bases/{kb_id}/search

```json
{
  "query": "时间切分协议",
  "top_k": 5,
  "document_ids": [],
  "filter": {"chunk_types": ["text", "table"], "year_min": 2020, "year_max": 2026},
  "allow_rerank_fallback": true
}
```

query 1–4000；top_k 1..20，默认5；document_ids 缺省/[] 表示全部当前 active 文档，最多100；filter 可省略，仅 chunk_types（text/table/formula）与 year_min/year_max（1900..2100，min≤max）。不接受字符串 SQL/Milvus 表达式。allow_rerank_fallback 默认 true，false 时 reranker 故障 503。

200 {items:RetrievalResult[],degraded:bool,degradations:Degradation[]}，trace 含 dense_rank/sparse_rank/fused_score/rerank_score（可空）/index_version。未匹配 items=[]；依赖异常503，删除屏障409。检索服务不返回 Research Evidence。Research 的冻结版本 allowlist 只能通过内部授权 context，公共 API 不能任意检索 retired 或他人版本。

## 5. CLI 契约

在 backend 运行 `uv run python -m cli ...`；CLI 为受信本地开发入口，用开发 owner，正式数据库使用需显式 owner 配置并校验存在，不能自动绕过归属。fake 默认内存隔离，--real 用真实依赖；doctor/dump 天然 real。配置均通过同一 settings 加载 backend/.env，进程环境优先。

| 命令 | 输入、执行语义与输出 |
|---|---|
| doctor --json | 检配置/PG/Milvus/MinIO/模型/Parser/执行器能力，输出逐项检查；readiness 不等于 E2E |
| run --brief FILE [--real] [--seed N] | 十字段 Brief；可用 --sources papers,web 与 --kb UUID 指定来源；调用 start_frozen 明确标记 CLI 确认，建 Run 并等待终态；不模拟 Clarify |
| phase PHASE --state FILE [--real] [--seed N] | validate PipelineState、phase 一致、最低前置，再用同 execute_phase/merge；不启动下一阶段、不写 Session/Report |
| dump SESSION_ID --json | 读最新 Run Checkpoint；有会话无快照 1 checkpoint_not_found；不要按 phase 倒序 |
| kb create NAME / kb list / kb get UUID / kb delete UUID | 复用管理 Service；异步删除返回接受状态，可轮询 get |
| ingest PDF --kb UUID [--real] [--key KEY] [--wait] | 复用 submit；--wait 才等终态，默认返回202对应IDs；key 默认 hash(文件+KB+profile)；确有持久副作用 |
| job get UUID / job retry UUID / job cancel UUID | 复用 Job Service |
| search QUERY --kb UUID [--top-k N] [--real] | 同 RetrievalResult 包装；不假装依赖失败为空 |

通用 --json 单个对象，stdout 无日志；--verbose 日志 stderr，默认不记录正文/prompt，显式调试也脱敏。--quiet 在 run 只输出最终 report，--json 时省略 events。fake 的 seed 固定 ID/时间/结果；真实模型输出不宣称确定。--seed 不影响 real 模型。

退出码0=该命令成功（含异步接受和合法零命中）；1=业务/执行失败；2=参数/Schema/phase 前置；3=依赖或环境故障。JSON 包装 {status:"ok"|"failed"|"usage_error"|"env_error",error:Error|null,...结果字段}，Error 与 HTTP.error 同字段。run 成功输出 session_id/run_id/final_report/events?；phase 输出完整 state/events/state_delta（改变后的顶层值，不是 JSON Patch）；dump 输出 state。signals 安全取消本 CLI 持有的 Run，不杀其他 lease_owner。

phase 前置：plan 完整 Brief；research 完整 plans/来源；analyze plans/分析输入（无需求可跳过，Observation 可空，但不可缺 key）；write plans+claims/evidence/sources/coverage（空证据仅允许明确 insufficient 章）；review 非空完整 draft_sections+同版 bindings+全部事实 map。最小输入是**语义前置与完整回链**，不是 dict 非空就算通过。

## 6. App Service 契约

所有方法 async，返回 typed 结果；业务失败抛 AppError(code,message,retryable,details)，不抛 HTTPException。owner 为服务端身份，不是请求正文。

| Service.method | 输入 → 输出 |
|---|---|
| AuthService.register/login/logout | DTO 所需字段 → 注册/凭证结果；hash/签发通过安全实现 |
| ResearchService.start | owner,ResearchRequest,key → AskResponse或ConfirmResponse |
| ResearchService.message | owner,session_id,MessageInput,key → Ask/Confirm |
| ResearchService.confirm | owner,session_id,ConfirmInput,key → Ask或Ready |
| ResearchService.start_frozen | owner,Brief,SourceSelection,key,origin=cli → Ready；与 confirm 共用冻结校验和事务 |
| ResearchService.status/report/cancel/resume | owner,session_id，加对应参数 → SessionView/ReportView/取消或Ready |
| SessionService.assess_initial/assess_round | immutable SessionInput,answer → CandidateSessionChange；不保存或 spawn |
| SessionService.validate_confirmation | SessionState,expected_brief_version → ValidatedFrozenInput |
| Orchestrator.execute_phase | PhaseInput,ExecutionContext → PhaseResult；不推进 phase |
| Orchestrator.run | run_id,lease_token → 执行循环，无 HTTP 返回 |
| KnowledgeBaseManagementService.create/get/list/update/delete/delete_document | owner+管理参数+key → Entity/列表/删除接受结果 |
| DocumentIngestionService.submit/get_job/retry/cancel | owner+KB/文档/文件引用/key 或 job_id → JobView/接受结果 |
| DocumentIngestor.execute | version_id,manifest,ExecutionContext,progress_callback → IngestionAttemptResult |
| KnowledgeRetrievalService.retrieve | owner,kb_id,SearchInput,version_scope? → RetrievalResponse |

ExecutionContext 包含 owner、run/job ID、固定版本配置、lease token、deadline、取消检查、预算预留、缓存调用执行器、诊断 emit。Agent 不持有 Repository；execution context 对 Agent 仅暴露必要工具与只读范围，缓存/进度 callback 不允许任意业务写入。

PhaseInput/PhaseResult 依 dataflow 阶段表切片；结果只包含该阶段允许字段；重试使用相同语义输入 hash，不能包含随机 timestamp 使缓存失效。

PhaseResult 为 `{phase, unit_id, input_hash, changes, degradations:Degradation[], failures:Failure[]}`；phase 必须与请求一致，changes 的白名单如下。输入使用 PipelineState 中相同名称的只读字段加 rework_targets；不将完整可写 State 交给 Agent。

| phase | changes 允许字段与合并规则 |
|---|---|
| plan | section_plans；校验五章后整体替换 |
| research | sources/evidence/claims/claim_evidence_links/quantitative_observations/section_coverage；身份集合去重追加，目标 Claim 状态/coverage 重算；同 ID 不同原文 hash 拒绝 |
| analyze | comparable_metrics/comparison_sets/analysis_artifacts/section_coverage；替换目标 requirement 的派生项，保留无关项；失效范围写入 rework_targets |
| write | draft_sections/draft_claim_bindings/draft_version；按目标章替换，未改章复制到新全局版本 |
| review | critic_feedback/reviewed_draft_version/review_verdict；保留旧反馈身份，复核后更新 resolved；不得改草稿 |

PhaseResult 不得返回 phase 路由、final_report、budget_used、lease 或 Session.status 的变更；其中顶层 phase 仅校验结果归属。预算与 unit_manifest 由执行协调器根据实际调用记录写入。Machine 根据已合并事实返回下一阶段/返工目标；只有交付事务能设置 final_report。

## 7. 内部 Port 契约

用 typing.Protocol 与项目数据类型，不泄漏 SDK对象。以下省略 async 声明但除 emit/clock 外均 async。事务句柄由 UnitOfWork 传入多个 Repository，不能每个方法自提交造成半冻结。

| Port | 最小方法与保证 |
|---|---|
| UnitOfWorkPort | transaction() → Tx；全体 PG Repository 共用；CAS/行锁条件提交 |
| UserRepositoryPort | create(User,tx)、get_by_email(email)、get_by_id(id)；唯一 email |
| ResearchRepositoryPort | get_session(owner,id)、commit_session_change(expected_revision,change,messages,brief,tx)、freeze_and_create_run(validated,tx)、claim_run(id,lease)、renew_lease、load_checkpoint(run,seq)、commit_checkpoint(token,expected_seq,state,run_change,tx)、publish_report(token,report,state,tx)、request_cancel、resume、scan_runnable；所有状态/报告/快照提交原子 |
| KnowledgeBaseRepositoryPort | create/get/list/update_cas、scan_creating_or_deleting、claim/renew/release_cleanup_lease |
| DocumentRepositoryPort | get/list_versions/list_visible_versions、reserve_version、save_chunks、activate_version(expected_revision,token,tx)、mark_deleting、cleanup_progress；版本切换锁 Document |
| IngestionJobRepositoryPort | create/get/claim/renew_lease/commit_progress/complete/fail/request_cancel/retry/scan_runnable；与 activate_version 共事务 |
| ContentStorePort | put(key,stream,expected_hash?) → ContentRef(key,sha256,size,media_type)；get(key) → stream；head(key)；delete(key)/delete_prefix(prefix)；重复同键不同 hash 拒绝 |
| DocumentParserPort | parse(ContentRef,ParserConfig) → ParsedDocument{blocks:[{type,content,location,caption?,notes?}],parser_version}；真实非空结构，不能空存根 |
| EmbeddingPort | encode_documents(anchors[]) / encode_query(query) → Embedding{dense:float[1024],sparse:map<int,float>,model_version}；真实 sparse 非空技术词样本 |
| VectorIndexPort | ensure_schema(index_version)、ensure_partition(kb)、upsert(rows[])、verify_rows(ids[],consistency=strong)、hybrid_search(QueryEmbedding,AuthorizedFilter,candidate_k) → VectorHit[]、delete_version/drop_partition；稳定主键，目标范围同读写 |
| RerankPort | rerank(query,[{chunk_id,content}],top_k) → RankedHit[]；只重排合法候选，不创 ID |
| LLMPort | complete(StructuredPrompt,response_schema,config) → validated object+usage；无 status 路由；异常 AdapterError |
| SearchPort | search(query,source_kind,limit) → SearchResult[]；候选有 title/url/snippet/metadata，无假正文 |
| FetchPort | fetch(SourceCandidate) → FetchedDocument{content_ref,final_url,media_type,hash,locations}；只允许合法外部来源 |
| RetrievalPort | retrieve(query,AuthorizedKnowledgeScope,top_k) → RetrievalResponse；桥接 Service，不自行访问 vector |
| CodeExecutionPort | execute(AnalysisSpec,immutable inputs,timeout) → ExecutionResult{status,output,files,logs,failure}；模板闭集，不接受任意代码字符串 |
| EventBusPort | subscribe(session) → 独立 async stream、emit(session,event)、unsubscribe；有界队列、非持久 |
| TaskRunnerPort | wake(resource_id)、register_owned_task、shutdown；wake 失败不能丢 PG ready |
| ClockPort | now_utc、monotonic；timeout 用 monotonic，lease 用数据库时间 |

PG 幂等请求和 tool_calls 可由 Repository 的共享 RequestStore/ToolCallStore 实现：reserve(owner,operation,key,request_hash)、complete(response)、get；reserve_call(run,call_key,budget)、save_call_result、list_uncertain。它们不是第二任务事实源，run/checkpoint 仍决定恢复。

AdapterError = {dependency,code,message,retryable,operation}；App 映射为 Failure 或公共 AppError。不可恢复解析/Schema 错误不能无限重试。查无资料是成功空值；依赖不可用必须错误。
