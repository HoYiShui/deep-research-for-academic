# Tasks: 网络安全学术研究 Deep Research 系统

**Input**: `/specs/001-deep-research-agent/`（plan.md / spec.md / data-model.md / contracts/api.md / research.md / quickstart.md）

**设计原则**：

- **纵向切片**：每一刀从 API 切到 infra，交付 e2e 能跑的极小功能
- **Walking skeleton 先行**：最薄贯穿四层、全 fake，先验证架构本身
- **契约先行**：先定端口契约 + 契约测试 → 再写 domain 纯逻辑 + 单元测试 → 再写适配器 → 最后集成
- **[P] = context 隔离**：任务自包含、无内部依赖，可"关起门单独完成"（串行实现，非并行人力）
- **完成定义**：有测试证明它对了，且构建仍是绿的

## 任务 DAG（概览）

```text
Phase 0 冻结点（骨架 + 端口契约 + fake + base.py）
        │
   ┌────┼─────┬─────┬─────┐   ← 端口冻结后，全部独立分支（context 隔离）
   ▼    ▼     ▼     ▼     ▼
  政策表 + agent×6   adapter×9   （各自内嵌契约 + 测试）
   └────┴─────┴─────┴─────┘
        ▼
Phase 2 集成切片（真 LLM → 溯源 → 持久化 → KB → 收尾，每刀换一个 fake）
        ▼
Phase 3 真实 agent 行为（契约冻结 → scout 补链/观察 → data_analyst/code_crafter/writer/critic 真实化 → 集成）
```

## Phase 0：冻结点（Walking Skeleton + 端口契约）

**Purpose**: 最薄贯穿四层的骨架，全 fake，spike 异步 SSE 管线；冻结端口契约 + 共享件

**⚠️ 这是收敛点**：此阶段结束前，任何 agent/adapter 都不能开始

- [x] T001 创建 backend/ 四层目录（interface/application/domain/infrastructure + tests/{contract,integration,unit}）+ backend/pyproject.toml（fastapi/asyncpg/pymilvus/httpx/uvicorn + pytest/pytest-asyncio/ruff/mypy）
- [x] T002 定义 domain/ports.py 全部端口接口（LLMPort/SearchPort/RetrievalPort/EmbeddingPort/VectorStorePort/RerankPort/ContentStorePort/FetchPort/EventSink/CodeExecutionPort）——**契约冻结**
- [x] T003 定义 application/ports.py（StateStorePort / CancellationPort）——**契约冻结**
- [x] T004 定义 domain/research/events.py（PhaseEvent/StepEvent + rework 事件 + PHASE_DISPLAY 单源映射）
- [x] T005 实现 domain/state.py 骨架（SessionState：brief_draft/clarification_history；PipelineState：section_plans/evidence/claims/phase 最小字段）
- [x] T006 实现 domain/machine.py 骨架（decide_status：critical 缺口→ask；next_phase：最简 happy path）
- [x] T007 实现 domain/research/agents/base.py（call_llm 注入 LLMPort + parse_json 脏 JSON 修复）——**共享件，6 个 agent 都依赖它**
- [x] T008 写契约测试 tests/contract/test_ports.py（每个端口 fake 实现必须满足的行为，先 FAIL）
- [x] T009 实现 infra/fake/ 全套 fake（fake_llm/fake_search/fake_state_store/fake_event_sink，满足端口契约，返回假数据）
- [x] T010 实现 application/session_service.py（clarify 循环，调 fake LLM + decide_status；**每 session 一个 asyncio.Lock 串行化**）
- [x] T011 实现 application/orchestrator.py（pipeline 循环，调 fake agent + advance 改状态+派生 SSE；**含 rework 事件发射**）
- [x] T012 实现 application/research_service.py（start 建 session + spawn orchestrator 后台任务）
- [x] T013 实现 application/sse.py + 进程内事件总线（dict[session_id→asyncio.Queue] + 0.5s drain）
- [x] T014 实现 interface/router/research.py（POST /research + GET /events）
- [x] T015 实现 application/bootstrap.py（组合根：构造 fake → 注入 service）
- [x] T016 验证 walking skeleton：POST /research → GET /events 收到 SSE 事件；确认依赖方向/事件总线/状态机循环/SSE 序列化/端口模式全对

**Checkpoint**: 骨架跑通 + 端口契约冻结 + base.py 就位 → 独立分支可开始

## Phase 1：独立分支（政策表 + agents + adapters，[P]=context 隔离）

**Purpose**: 端口冻结后，每个单元自包含、可单独完成；各带测试，主干保持绿

### 政策表（最该单测覆盖的纯函数）

- [x] T017 [P] 实现 machine.route_after_review 回流政策表（total：missing_source×severity×fillable 全组合 + hallucination→retract+补证 + 兜底 revise）+ 单测 tests/unit/test_route.py（每一行 + 兜底行都测）

### Agents（domain/research/agents/，每个自包含：契约 + 单元测试）

- [x] T018 [P] 实现 architect.clarify()（输入 brief_draft+answer → 输出 missing_fields/questions/brief_patch/assumptions，**不产 status**）+ 单测 tests/unit/test_clarify.py
- [x] T019 [P] 实现 architect.plan()（冻结 brief → section_plans）+ 单测 tests/unit/test_plan.py
- [x] T020 [P] 实现 scout.research()（SearchPort paper/web + RetrievalPort local；抽 Evidence 带来源定位、建 Claim+ClaimEvidenceLink、source_id+location+quote 去重、gap_fill）+ 单测 tests/unit/test_scout.py
- [x] T021 [P] 实现 data_analyst.analyze()（归一 evaluation_context → ComparableMetric，判 compatible/partial/incompatible）+ 单测 tests/unit/test_data_analyst.py
- [x] T022 [P] 实现 code_crafter.analyze()（固定模板 → AnalysisArtifact，经 CodeExecutionPort）+ 单测 tests/unit/test_code_crafter.py
- [x] T023 [P] 实现 writer.write_report()（读 Claim/Evidence → DraftSection + DraftClaimBinding，每结论绑定 evidence_id）+ 单测 tests/unit/test_writer.py
- [x] T024 [P] 实现 critic.review()（产出 issue_type/severity/fillable 判断，**不产 required_action**）+ 单测 tests/unit/test_critic.py

### Adapters（infrastructure/，每个自包含：契约 + 契约测试）

- [x] T025 [P] 实现 infra/llm/deepseek.py（LLMPort，Anthropic 兼容 + JSON mode）+ 契约测试 tests/contract/test_llm.py
- [x] T026 [P] 实现 infra/search/bocha.py + arxiv.py（SearchPort）+ 契约测试 tests/contract/test_search.py
- [x] T027 [P] 实现 infra/embedding/bge_m3.py（EmbeddingPort，dense+sparse）+ 契约测试 tests/contract/test_embedding.py
- [x] T028 [P] 实现 infra/reranker/bge_reranker.py（RerankPort）+ 契约测试 tests/contract/test_rerank.py
- [x] T029 [P] 实现 infra/vector/milvus.py（VectorStorePort，hybrid + partition per KB）+ 契约测试 tests/contract/test_vector.py
- [x] T030 [P] 实现 infra/storage/postgres.py + storage/models/（StateStorePort：sessions/messages/briefs/reports/phase_snapshots 表）+ 契约测试 tests/contract/test_state_store.py
- [x] T031 [P] 实现 infra/storage/memory.py（CancellationPort，进程内 dict）+ 契约测试 tests/contract/test_cancel.py
- [x] T032 [P] 实现 infra/sandbox/docker.py（CodeExecutionPort：一次性容器 + --network=none/--read-only/--cap-drop=ALL/资源上限）+ 契约测试 tests/contract/test_execution.py
- [x] T033 [P] 实现 infra/parser/pdf.py + ContentStorePort/FetchPort 实现（MinIO 本地读 / arXiv·web 外部拉）+ 契约测试 tests/contract/test_content.py

> **冒烟验证**：每完成一个 adapter，顺手把它塞进骨架对应 fake 位置跑一次（不等到 Phase 2 统一换）——在写第 3 个 adapter 时发现契约错了，而不是写完第 15 个才发现。

**Checkpoint**: 政策表 + agent + adapter 全部单测/契约测试绿

## Phase 2：集成切片（每刀换一个 fake，失败语义逐刀织入，系统保持绿）

**Purpose**: 按风险序逐刀把 fake 换成 real，每刀有集成测试 + 该刀依赖的失败语义，主干永远绿

### 真 LLM 切片（风险最高：LLM 输出脏 + JSON mode）

- [x] T034 spike deepseek Anthropic 兼容 + JSON mode 稳定性（50 行脚本：判断抽取 + 政策路由输入输出）
- [x] T035 集成：bootstrap 把 fake_llm 换成 deepseek，wire architect/critic；**织入 LLM 失败语义**（重试 2 次指数退避→耗尽终止）；集成测试 tests/integration/test_slice_llm.py

### 溯源切片（高风险：溯源链 = 章程第一条「可溯源」核心）

- [x] T036 集成：bootstrap 把 fake_search 换成 arxiv/bocha，wire scout；**织入搜索失败语义**（超时→重试 1 次→coverage 缺口；服务不可用→降级跳过该源）；集成测试 tests/integration/test_slice_retrieval.py

### 持久化切片（中风险：两 state 冻结交接 + 恢复）

- [x] T037 集成：bootstrap 把 fake_state_store 换成 postgres，验证两 state 冻结交接 + phase_snapshots 同 phase 取最新 + 崩溃恢复；**织入 PG 不可用→终止（真相源不可丢）**；集成测试 tests/integration/test_slice_persistence.py

### KB 切片（中高风险：BGE-M3 sparse + Milvus hybrid）

- [x] T038 spike BGE-M3 sparse + Milvus hybrid（pymilvus 支持 + RRF 融合，50 行脚本）
- [x] T039 集成 KB 检索：fake_vector/embedding/reranker → milvus/bge_m3/bge_reranker，实现 RetrievalPort + /knowledge-base/search；**织入 Milvus 不可用→降级（跳过 local）**；集成测试 tests/integration/test_slice_kb_search.py
- [x] T040 实现 KB 入库：knowledge_base_service.ingest（asyncio.to_thread：parse→chunk→embed→store + 进度持久化 documents 表 + 启动扫 processing→failed）+ /knowledge-base/documents 端点 + 集成测试 tests/integration/test_slice_kb_ingest.py

### 收尾切片（沙箱 + 认证）

- [x] T041 集成：wire 沙箱（docker.py，code_crafter 执行）+ 认证（/auth/register/login，bcrypt/argon2 + JWT/cookie）+ 集成测试 tests/integration/test_slice_reliability.py
- [x] T042 跑通 quickstart.md 最小闭环（认证→澄清→流水线→报告），对照 docs/cases/case-1-idea-exploration.md 定位差距

**Checkpoint**: 完整系统 e2e 可跑，全部测试绿

## Phase 3：真实 agent 行为（终态设计落地）

**Purpose**: 把 Phase 1 的存根 agents 升级为按 data-model.md + docs/architecture（02/04/05/06 号）的真实实现。坚持：**纯工人**（只产数据，控制流仍在 machine.py 政策层）、**契约先行**、**单测**；对齐章程「可溯源」（Claim→Evidence→Source 全链 id 回链）。

> 权威源以 spec（plan.md/data-model.md）为准；dataflow.md 及 docs/architecture 是较早草稿，若冲突（如 critic 的 `required_action`）以 spec 为准。

### 契约冻结（收敛点）

- [ ] T043 冻结终态 PipelineState 契约：`state.py` 补齐 data-model.md 字段（`sources`/`claims`/`claim_evidence_links`/`quantitative_observations`/`section_coverage`），并把 `evidence`/`comparable_metrics`/`analysis_artifacts`/`draft_sections` 从 list 改为 dict-keyed（按 id 回链）+ 单测 `tests/unit/test_state.py`。**收尾（漂移 C-4/C-5）**：字段 `brief`→`research_brief`；phase→worker 分发从 orchestrator 的 if/elif 链迁回 machine.py 的 `WORKERS` 表

### Agents（每个自包含：契约 + 单元测试）

- [ ] T044 [P] 实现 scout 的 SourceRecord 登记 + Claim/ClaimEvidenceLink 建链（source_id→title/authors/version/source_tier 去重；从证据提炼论断 + supports/refutes/limits 关系）+ 单测 `tests/unit/test_scout_claims.py`
- [ ] T045 [P] 实现 scout 的 QuantitativeObservation 抽取（结果表单元格 → observation_id/evidence_id/row_key/column_key/value/uncertainty/statistic 结构化投影）+ 单测 `tests/unit/test_scout_observations.py`
- [ ] T046 [P] 实现 data_analyst.analyze 真实化（collect observations → normalize evaluation_context → check comparability → comparable_metrics：compatible/partial/incompatible + reasons）+ 单测 `tests/unit/test_data_analyst.py`（扩展）
- [ ] T047 [P] 实现 code_crafter.analyze 受控模板（operation 闭集 = comparison_matrix/pairwise_delta/plot/statistic/aggregation，图表类型作 plot 参数 → AnalysisArtifact{input_metric_ids/input_evidence_ids/operation/code_or_recipe/output/execution_status}，禁 LLM 自由代码，仅 compatible 输入）+ 单测 `tests/unit/test_code_crafter.py`（扩展）
- [ ] T048 [P] 实现 writer.write_report 真实化（按 `01-contract.md §5` + `docs/contracts/report-skeleton.md` 的统一骨架 + 任务专属第 3 节写正文 DraftSection，每条关键结论绑定 evidence_id + 程序级 DraftClaimBinding{section_id/statement_id/claim_ids/cited_evidence_ids/artifact_ids}，final_report 落统一骨架）+ 单测 `tests/unit/test_writer.py`（扩展）。**收尾（漂移 C-6）**：补 `revise_report()`（revise 回流落 write 时走它，而非重跑 write_report）
- [ ] T049 [P] 实现 critic.review 真实化（三层复核：来源/证据、论断/条件、分析/表达 → CriticFeedback{issue_type∈missing_source/comparability_violation/overclaim/hallucination/outdated/logic_error, severity, fillable}，**不产 required_action**）+ 单测 `tests/unit/test_critic.py`（扩展）

### 集成切片

- [ ] T050 集成：orchestrator 按终态接线（research 阶段产出 sources/claims/observations；analyze 喂真 observations→metrics；write/review 用真 writer/critic；回流仍走 machine 政策表）+ 集成测试 `tests/integration/test_slice_agents.py`
- [ ] T051 跑通 quickstart 对照 case-1：报告正文非占位符、每条关键结论可回链证据（SC-002 抽查）+ 更新 `scripts/smoke_e2e.py`

**Checkpoint**: 报告正文真实落地，全链可溯源，全部测试绿

## Dependencies & Execution Order

### 切片顺序（由风险决定，非文档顺序，用描述性命名）

```
骨架(spike 异步管线) → 真 LLM(JSON mode) → 溯源(可溯源核心) → 持久化 → KB → 收尾
```

### 依赖关系

- **Phase 0 冻结点**：无依赖，最先做；冻结后分叉
- **Phase 1 独立分支**：依赖 Phase 0（端口契约 + base.py）；互相独立（[P]=context 隔离）
- **Phase 2 切片**：依赖 Phase 1；每刀换一个 fake，串行推进，每刀系统保持绿
- **Phase 3 真实 agent**：依赖 Phase 2；T043 契约冻结后 T044–T049 独立分支（[P]），T050–T051 集成串行

### 失败语义的归属（逐刀织入，不 retrofit）

- LLM 失败 → 真 LLM 切片
- 搜索失败 → 溯源切片
- PG 不可用 → 持久化切片
- Milvus 不可用 → KB 切片

### [P] 的含义（context 隔离，非并行人力）

- 标 [P] 的任务自包含、无内部依赖，可"关起门单独完成"（不需要加载整个项目 context）
- 串行地逐个实现，但每个任务只需读自己的描述 + 依赖的端口契约

## Implementation Strategy

### Walking Skeleton First

1. Phase 0（骨架 + 冻结 + base.py）→ spike 异步管线，验证架构本身
2. 架构有错，骨架先断——这是最早、最便宜的验证点

### 冒烟验证（Phase 1 内）

每完成一个 adapter，顺手塞进骨架对应 fake 位置跑一次，尽早发现契约错

### Incremental（每刀换一个 fake）

1. Phase 1 写完政策表 + agent + adapter（各带测试，绿）
2. Phase 2 逐刀集成（真 LLM→溯源→持久化→KB→收尾），每刀集成测试绿，主干永远绿

### 完成定义

每个任务 =「有测试证明它对了，且构建还是绿的」；不是「代码写完了」
