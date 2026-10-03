# Research: 技术选型决策

> Phase 0 输出。每条决策记录 Decision / Rationale / Alternatives。

## 0. 架构模式：SSOT + 派生事件 + 纯工人

- **Decision**: ResearchState 是唯一事实来源（SSOT）；SSE 事件从状态转移**派生**（`advance` 改状态 + 派生事件同源）；Agent 是纯工人（只交结果、不读写 phase）；转移规则集中在显式状态机。
- **Rationale**: 消除「事件日志 vs 状态」的漂移（结构上不可能漂）；Agent 可单测；控制流只有一处。
- **Alternatives**: 事件溯源（event sourcing，事件为真相）——本场景 SSE 是 UI 投影，不需要事件当真相、也不需要全量事件重放。

## 1. 工作流编排：V1 手写状态机，V2 引入 LangGraph

- **Decision**: V1 用手写显式状态机（machine.py 纯函数 + orchestrator.py 控制流），不引入 LangGraph；V2 再上 LangGraph 简化编排。
- **Rationale**: V1 的核心难点是 SSOT + 状态派生事件 + phase 级快照这套状态模型，手写更可控、不被 LangGraph 的状态模型牵着走；Agent 已是纯工人（input→result），V2 迁移成 LangGraph 节点几乎零成本。
- **Alternatives**: V1 直接上 LangGraph（细粒度 checkpoint V1 用不上，且其状态模型与 SSOT 设计可能冲突）；Temporal（过重）；Prefect（偏数据管道）。

## 2. 跨请求状态：进程内态（V1）/ Redis（V2）

- **Decision**: V1 单进程用进程内 `dict`（SSE 事件总线 = `dict[session_id → asyncio.Queue]`；取消标志 = `dict[session_id → bool]`）；V2 多进程/多实例改用 Redis（pub/sub + 取消标志）。
- **Rationale**: V1 单 worker（`uvicorn --workers 1`）部署下进程内态最简单且满足需求；扩展时才需要 Redis 解耦任务执行与事件推送。
- **Alternatives**: V1 直接上 Redis（单进程用不上、徒增运维）；PostgreSQL 独占（查询慢，不适合高频临时态）。

## 3. 业务快照持久化：PostgreSQL JSONB

- **Decision**: PostgreSQL JSONB 持久化 phase 级业务快照、返工目标与失败信息。
- **Rationale**: 需持久、可查询、可审计的阶段快照；JSONB 适合 schema 随版本演进的快照。业务真相（ResearchState）不可重建，永久存活。
- **Alternatives**: 文件存储（并发与查询差）；单机 SQLite（查询/审计弱，不便于多服务共享）。

## 4. 本地知识库：Milvus

- **Decision**: Milvus 作为本地知识库向量检索后端。
- **Rationale**: 原型已在用；需向量检索上传论文/组内资料/实验记录（`docs/architecture/03-deepscout.md` 的 `local_search`）。
- **Alternatives**: pgvector（复用 PostgreSQL 但检索性能弱于专用向量库）；FAISS（单机文件，无服务化）。

## 5. 进度推送：SSE

- **Decision**: SSE 按事件推送阶段进度与增量结果。
- **Rationale**: 单向服务器→客户端推送，简单、HTTP 原生、适合「进度可见」；比 WebSocket 轻。
- **Alternatives**: WebSocket（双向，但本场景无需客户端回推）；轮询（浪费）。

## 6. LLM：deepseek（Anthropic 兼容接口）

- **Decision**: deepseek-v4-flash，经 Anthropic 兼容 API（`https://api.deepseek.com/anthropic`）调用。
- **Rationale**: 已在用；成本可控。接口层抽象，便于替换模型。
- **Alternatives**: 其他闭源/开源模型（均可替换，不影响架构）。

## 7. 检索源：web search + paper search + local

- **Decision**: 论文检索（arXiv API / 学术索引）、网页搜索（Bocha）、本地知识库（Milvus）。
- **Rationale**: `docs/architecture/03-deepscout.md` 定义的三类资料源（paper_search / web_search / local_search）。
- **Alternatives**: 单一 web 检索（无法覆盖论文与本地资料）。

## 8. 测试：pytest

- **Decision**: pytest + pytest-asyncio。
- **Rationale**: Python 标准；章程要求「合并前必须通过测试」。
- **Alternatives**: unittest（内置但生态弱）。

## 9. RAG 选型：本地知识库（入库 → 检索 → 精排）

- **Decision**: MinerU 2.5 解析 → 语义结构切分（表格/公式独立单元）→ BGE-M3 混合嵌入（dense+sparse）→ Milvus 存储（hybrid）→ BGE-reranker-v2-m3 精排。
- **Rationale**: 学术论文重表格/公式，MinerU 能结构化抽出；BGE-M3 多语 + 混合检索匹配技术文档；同族 reranker 自洽。
- **Alternatives**: 通用 PDF 解析（pdfplumber / PyMuPDF，抽不出表格公式结构）；OpenAI embedding（dense-only，无 sparse，技术词匹配弱）；FAISS / pgvector（无原生 hybrid）。

### 核心原则：语义锚 + 原文分离

数字/符号的语义远不如自然语言丰富，所以每个原子 chunk 需要「自然语言的语义表示」做嵌入锚，原文只做存储、最后喂 LLM：

| chunk 类型 | 嵌入锚（语义） | 存储（原文） |
|---|---|---|
| text | 前缀 = title + section_heading | 正文段落 |
| table | caption + 列头 + 表注（Note） | 完整 markdown 表格 |
| formula | 引入句 + 解释句（句子级 window） | LaTeX 源码 |

表格的「表注/Note」常被忽略但语义极丰富（口径、条件、mean±std），必须并入锚。公式的 context 按句子取「引入句 + 解释句」，不用固定 token 窗（避免切半句）。

### 分项选型

| 环节 | 选型 | 说明 |
|---|---|---|
| 解析 | MinerU 2.5（magic-pdf，1.2B） | 结构化抽表格（HTML/LaTeX）+ 公式（LaTeX） |
| 切分 | 语义结构切分 | 表格/公式作独立不可再分单元 |
| 嵌入 | BGE-M3 | dense（语义）+ sparse（精确技术词）双向量 |
| 存储 | Milvus | 原生 hybrid search |
| 精排 | BGE-reranker-v2-m3 | 检索后精排 |
| metainfo | paper_id / title / authors / year / venue / doi / arxiv_id / section_heading / page / chunk_type | 溯源 + 过滤 + 引用 |

### 两阶段检索：bi-encoder 召回 + cross-encoder 精排

信息压缩的损耗层级：text → token → vector（损耗递增）。bi-encoder（BGE-M3）把 query/chunk 各自压成向量，
只在「向量点积」层比较（单向量瓶颈、无 token 交互），故快而粗；cross-encoder（BGE-reranker-v2-m3）在 token
层做全交互打分，故慢而准。流程：bi-encoder 粗召回 top-20 → cross-encoder 精排取 top-5。RRF 只融合 dense+sparse
两个「向量层」信号，不跳出向量相似度层；精排才是从向量层跳到 token 层。

### 多租户隔离与存储分层

- Milvus 用「单 collection + partition per KB」隔离（`drop partition` = 删整库），不按用户开容器。
- 存储分层（**PG 是元数据真相源**）：PostgreSQL = chunk/文档元数据真相源（chunks 表含 object_key）；Milvus = 向量 + 去规范化元数据子集（kb_id/paper_id，供检索过滤）；MinIO = PDF 原文 + chunk 文本。`chunk_id → object_key` 映射在 PG。
- 检索命中 chunk_id → ContentStorePort.get 从 MinIO 拉原文。
- 删除顺序：PG → Milvus → MinIO（Milvus 是 PG 的副本，避免孤儿数据）。
