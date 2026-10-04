# 数据流（Dataflow）

> 细粒度数据流：模块函数 + 数据变换 + 数据类型。目的：让后续实现（tasks / implement）有据可依。
> 对应架构见 `plan.md`，RAG 选型见 `research.md` 决策 #9。

## 1. DeepResearch 端到端数据流

### 阶段 0：入口（建会话 + 初始 Clarify）

```text
【前端 browser client】
  用户输入 query（+ 可选 task_type / sources）→ 点「开始研究」

POST /research
  body: { query, task_type?, sources? }

【interface】router/research.py
  ① 认证中间件 → 从 cookie/token 解出 user_id
  ② Pydantic 校验 ResearchRequest
  ③ research_service.start(user_id, req)

【application】research_service.py
  ④ start()：
     - 生成 session_id（uuid）
     - 初始化 SessionState{ phase: clarify, brief_draft: 空, clarification_history }（归 session_service，存 sessions 表）
     - StateStorePort → PostgreSQL 写 sessions 表（session_id + user_id + status=clarify）
     - 用原始 query 执行 clarify_initial；Architect 只产 missing_fields / questions /
       brief_patch / assumptions，machine.decide_status 决定 ask 或 confirm

【interface】
  ⑤ 同步返回 201：
       - ask → { session_id, status: ask, questions, missing_fields, brief_draft }
       - confirm → { session_id, status: confirm, research_brief }
       （关键：这里只建会话并完成同步 Clarify，不启动 pipeline）
```

### 阶段 1：澄清（session 多轮，经 HTTP）

```text
用户发消息 → POST /research/{session_id}/messages { content }

【application】session_service.clarify 循环：
  ⑦ architect.clarify(brief_draft, answer)              # 只产判断
       → { missing_fields, questions, brief_patch, assumptions }
  ⑦' decide_status(missing_fields) → ask/confirm # 代码政策（machine.py）
  ⑧ status=ask → HTTP 返回 questions / missing_fields / brief_draft → 用户答 → 回 ⑦
      （持久化 brief_draft + history）
  ⑨ status=confirm → HTTP 返回完整 research_brief，等待用户审核

【确认】
  ⑩ POST /research/{session_id}/confirm { accepted: true }
      → 再次校验 → 冻结 ResearchBrief（写 briefs 表）→ 后台启动 pipeline
      → HTTP 返回 { status: ready, sse_url }
  ⑪ accepted=false + feedback → 回到 Clarify

【前端】
  ⑫ 收到 ready 后才连接 GET /research/{session_id}/events（带 cookie）
```

### 阶段 2：研究流水线（长跑，SSE 推进度）

```text
冻结 brief → orchestrator.run(brief)  # 冻结 Brief 是 pipeline 的输入；orchestrator 初始化 PipelineState（section_plans/evidence/...）
  → machine.next_phase 决定 phase → 执行 phase steps → 回流 → done
  （细粒度见 §2）
  每个 phase 边界：改 state.phase → 派生 PhaseEvent → SSE → 写快照 → 查取消
```

### 阶段 3：交付

```text
phase=done → 写 FinalReport → reports 表
  → 派生 DoneEvent{ report_url } → SSE 推给前端
【前端】收到 done → GET /research/{session_id}/report → 渲染报告
```

## 2. 研究流水线（5 个 phase 细粒度）

### 状态机总览

```text
orchestrator.run(brief) 内部循环（state = PipelineState）：
  machine.next_phase(state) → 决定下一 phase
  orchestrator.advance(state, phase) → 执行 steps + 改状态 + 派生 SSE + 写快照
  直到 phase = done
```

> **agent 是纯函数（slice → result），不写 state**：orchestrator.advance() 调 agent 拿 result，再把 result merge 回 state（谁 merge、在哪 merge = advance 内部，统一一处）。下文各 phase 的「写回」均为「advance 把 agent result merge 回 state」的简写。

### Phase 0：plan（规划，architect.plan → section_plans）

```text
architect.plan(brief) → section_plans   # 冻结 brief → 逐章节计划（objective / sub_questions / retrieval_anchors / evidence_requirements）
写回 state.section_plans
→ machine.next_phase → research
```

### Phase 1：research（DeepScout 检索）

```text
for section in state.section_plans:
  scout.research(section, emit)：
    ① 由 section.sub_questions 生成检索 query 列表
    ② for q in queries:
         SearchPort.search(q) → 候选（paper/web 两源并发）
         RetrievalPort.retrieve(q, kb_id, top_k) → 本地库 chunk（embed→hybrid→rerank）
         ContentStorePort.get(chunk_id)（cache-hit）/ FetchPort.fetch(source, doc_ref)（cache-miss）→ 原文（只对高价值/缺原文的调用）
    ③ 抽 Evidence{ evidence_id, source_id, evidence_type, location, quote }
    ④ 建 Claim + ClaimEvidenceLink{ relation: supports/refutes/limits }
    ⑤ 去重（source_id+location+quote）
    ⑥ 检查 coverage → 缺口 → gap_fill 补查（预算内）
    ⑦ 写回 state：sources/evidence/claims/links/observations/section_coverage
    emit(StepEvent{ section_id, stage, detail })
→ machine.next_phase → analyze
```

### Phase 2：analyze（口径归一 + 计算）

```text
data_analyst.analyze(state, emit)：
  对需量化的 section：
    ① 收集 QuantitativeObservation
    ② 归一 evaluation_context（task/dataset/split/metric/threshold/baseline）
    ③ 判可比性 → ComparableMetric{ comparability: compatible/partial/incompatible, reasons }
    ④ 写回 comparable_metrics + coverage 缺口
code_crafter.analyze(state, emit)：
  对 compatible 的 ComparableMetric：
    ① 选受控模板（comparison_matrix/pairwise_delta/plot）
    ② 校验输入回链（metric_id/evidence_id）
    ③ 沙箱执行计算/制图
    ④ 写回 AnalysisArtifact{ input_metric_ids, input_evidence_ids, output, execution_status }
→ machine.next_phase → write
```

### Phase 3：write（撰写）

```text
writer.write_report(state, emit)：
  for section：
    ① 读 Claim + Evidence + ComparableMetric + AnalysisArtifact
    ② 写 DraftSection（每个关键结论绑定 evidence_id）
    ③ 生成 DraftClaimBinding{ section_id, statement_id, claim_ids, cited_evidence_ids, artifact_ids }
  写回 draft_sections + draft_claim_bindings
→ machine.next_phase → review
```

### Phase 4：review（审阅 + 回流路由）

```text
critic.review(state, emit)：
  for binding in draft_claim_bindings：
    ① 查来源真实性（binding → Evidence → SourceRecord 回链）
    ② 查论断条件（Claim.conditions 完整）
    ③ 查越界（ComparableMetric.comparability）
    ④ 产出 CriticFeedback{ issue_id, target, issue_type, severity, fillable }（只产判断，不产 required_action）
  写回 critic_feedback

machine._route_after_review(state)：
  - 确定性政策表路由（total：全组合有定义 + 兜底）：
      missing_source + critical + fillable    → re_research（补查）
      missing_source + critical + !fillable   → acknowledge_limit（收紧边界）
      comparability越界 + critical            → re_analyze（重算）
      表述越界 + major                         → revise（修订）
      * + any                                  → revise（兜底）
  - 无 issue 或轮次达上限 → done
```

### 横切（每个 phase 边界都发生）

```text
orchestrator.advance(state, phase)：
  ① 改 state.phase
  ② 派生 PhaseEvent → EventSink.emit → asyncio.Queue →（0.5s drain）→ SSE
  ③ StateStorePort.save_snapshot(phase, state) → PostgreSQL phase_snapshots
  ④ CancellationPort.is_cancelled() → 进程内标志查取消 → 若取消则停
```

## 3. 知识库离线入库

```text
POST /knowledge-base/documents（multipart 上传 PDF）
  body: { kb_id, file }

【interface】router/knowledge_base.py
  ① 认证中间件 → user_id，校验 kb 归属
  ② 接收文件
  ③ knowledge_base_service.ingest(user_id, kb_id, file)

【application】knowledge_base_service.py
  ④ ingest()：
     - 生成 document_id
     - 存 PDF 原文 → MinIO（对象存储）
     - 写 documents 表（PostgreSQL）：document_id + kb_id + user_id + status=processing + progress=0

  ⑤ asyncio.to_thread(ingest_pipeline)：  # CPU 阻塞（MinerU/BGE-M3），包线程池，不阻塞事件循环
     - ParserPort.parse(pdf) → { 正文 + 表格(HTML/LaTeX) + 公式(LaTeX) }   ← MinerU
     - chunking（语义切分）→ chunks（表格/公式作独立单元）
     - for chunk in chunks：
         构造语义锚（text=title+section / table=caption+列头+表注 / formula=引入句+解释句）
         EmbeddingPort.embed(锚) → { dense, sparse }
         VectorStorePort.insert(kb_id, chunk_id, vector, metainfo)
     - 存 chunk 文本 → MinIO
     - 每步更新 documents 表 progress（持久化，供轮询读）
     - 完成后 status=done

【interface】
  ⑥ 返回 202 { document_id, status: processing }

【轮询】GET /knowledge-base/documents/{doc_id}
  → 从 documents 表读 { status, progress }（进度持久化，不靠内存）

【崩溃恢复】启动时扫 documents 表：status=processing 的 → 标 failed（或 pending 重入队）
```

## 4. 知识库在线检索（RAG）

```text
POST /knowledge-base/search
  body: { query, kb_id, top_k=20, rerank=true }

【interface】router/knowledge_base.py
  ① Pydantic 校验 KnowledgeBaseSearchRequest
  ② 认证中间件 → user_id，校验 kb 归属

【application】knowledge_base_service.py
  ③ search() → 调 RetrievalPort.retrieve(query, kb_id, top_k)（下面 ④-⑨ 是 RetrievalPort 内部实现）

【domain/ports】
  ④ q = EmbeddingPort.embed_query(query) → QueryEmbedding{ dense: float[1024], sparse: {token_id: weight} }
     （dense 管语义、sparse 管精确技术词）

【infrastructure】embedding/bge_m3.py
  ⑤ BGE-M3 跑两路：dense encoder → dense；sparse encoder → 词级权重

【domain/ports】
  ⑥ candidates = VectorStorePort.hybrid_search(kb_id, q, top_k)
       → [{chunk_id, dense_score, sparse_score, fused_score}] ×20

【infrastructure】vector/milvus.py
  ⑦ hybrid_search：dense 走 ANN + sparse 走倒排 → RRF 融合 → top-20

【domain/ports】（rerank=true）
  ⑧ ranked = RerankPort.rerank(query, candidates, top_k=5)

【infrastructure】reranker/bge_reranker.py
  ⑨ cross-encoder 对 (query, chunk.text) 逐对打分 → 重排取 5

【application】
  ⑩ chunk_id → ContentStorePort.get(chunk_id) → chunk 原文（MinIO）+ metainfo（PG chunks 表）

【interface】
  ⑪ 返回 KnowledgeBaseSearchResponse[{chunk_id, text, score, paper_id, title, page, section_heading, chunk_type}]
```
