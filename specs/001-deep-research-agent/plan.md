# Implementation Plan: 网络安全学术研究 Deep Research 系统

**Branch**: `001-deep-research-agent` | **Date**: 2026-09-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-deep-research-agent/spec.md`

## Summary

将「网络安全学术研究 Multi-Agent Deep Research 系统」落地为可部署 Python Web 服务。
架构：**四层整洁架构**（interface / application / domain / infrastructure）+ **SSOT**（状态=唯一事实）
+ **显式状态机** + **纯工人 Agent** + **端口/适配器**。**session 是交互入口**（承载 clarify 多轮
+ pipeline 进度流）；clarify 的**智能在 architect、循环在 session_service**；pipeline 用 machine.py
显式状态机。V1 手写编排（不引入 LangGraph），V2 再上 LangGraph 简化。

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastAPI（Web/SSE）、asyncpg（PostgreSQL）、pymilvus（本地知识库）、httpx（检索）、Anthropic 兼容 LLM 客户端（deepseek）。LangGraph、Redis 留到 V2。

**Storage**（按数据生命周期分层）:

| 数据 | 落位 | 生命周期 |
|---|---|---|
| SSE 进度事件 | 进程内存 `asyncio.Queue` | 瞬态，推送完即弃 |
| 取消标志 | 进程内 dict（V1）/ Redis（V2） | 跨请求共享 |
| 业务快照（phase 级）+ 业务真相（ResearchState） | PostgreSQL | 永久，不可重建 |
| 本地知识库向量 | Milvus | 持久 |
| 审计历史 | **V2**（暂不设计） | — |

**Testing**: pytest + pytest-asyncio（章程：合并前必须通过测试）

**Target Platform**: Linux server（Docker 部署）

**Project Type**: web-service（后端 API + SSE + 前端）

**Performance Goals**: 单次调研在检索预算内完成；SSE 事件延迟可感知（<1s 内推送）

**Constraints**: 单机/单卡算力；检索轮次与预算有界；phase 级快照恢复（检索幂等去重，中断重跑一个 phase 安全）

**Scale/Scope**: 课题组内使用；v1 单租户、无复杂权限

## Architecture

### 四层 + 依赖方向（永不反向）

```text
interface → application → domain → ports（抽象接口）
                                    ↑ 实现
                               infrastructure（适配器）
```

| 层 | 职责 | 依赖 |
|---|---|---|
| interface | router + DTO，只做 HTTP 翻译 | application |
| application | 用例/编排，唯一控制流（orchestrator） | domain |
| domain | 核心资产，纯逻辑，不碰外部 | ports（抽象） |
| infrastructure | 端口实现（适配器） | 无（实现端口） |

### SSOT + 派生事件 + 纯工人

- **state.py = SSOT**：ResearchState 是唯一事实来源；SSE 事件从状态转移**派生**（`advance` 改状态 + 派生事件同源），漂移在结构上消失。
- **agents = 纯工人**：`agent(slice, emit) -> result`，只交结果、不读写 phase、不持有跨 turn 状态。
- **machine.py = 纯政策层**：clarify 与 pipeline 两条状态机的确定性规则集中一处（`decide_status` + `WORKERS` + `next_phase` + `_route_after_review` 政策表）；phase 序列 = plan → research → analyze → write → review；critic 只产 `issue_type × severity × fillable` 判断，路由是 total 政策表（含兜底），LLM 不驱动控制流。

### 两个交互模式（两种状态机职责）

| 阶段 | 交互模式 | 状态机 | 载体 |
|---|---|---|---|
| Clarify（brief 未满足） | 多轮、同步（反问→用户答→再问） | 简单循环（session_service 驱动） | session |
| Pipeline（brief 已冻结） | 长跑、异步（SSE 推进度） | 显式状态机（machine.py） | session（看进度） |

**Clarify 定位**（LLM 产判断、代码应用政策）：
- `architect.clarify(brief_draft, answer) -> {missing_fields, questions, brief_patch, assumptions}`——**只产判断，不产 status**。
- `decide_status(brief_draft, missing_fields) -> ask/confirm/ready`——**纯代码政策**（有 critical 缺口→ask，否则→保守默认→ready），在 machine.py。
- 循环在 session_service（`while status != ready: clarify → 问用户 → 合并 answer → 持久化`）。
- **状态持久化**：brief_draft + clarification_history 经 StateStorePort 落 PostgreSQL（clarify 跨多个 HTTP 请求，不能只活在内存）；每轮 Q&A 是 append-only，由 session 的 messages 表承载（天然审计）。
- **brief_draft 是结构化压缩态**：每轮把 answer 折进 brief，下一轮只看 `brief_draft + 最新 answer`，不重读全文 → context 有界，无 ReAct 式膨胀。

**会话 ↔ 流水线边界**（依赖无环，research_service 是唯一中介；两个 state，冻结是交接点）：

```text
# 入口 1：建会话（POST /research，只建 session，不跑 clarify）
research_service.start():
    return session_service.create()     # SessionState{ brief_draft, history }，返回 status=clarify

# 入口 2：推进一轮 clarify（POST /messages，每轮一次）
session_service.on_message(session, answer):
    status = clarify_round(session, answer)          # architect.clarify + decide_status
    if status == ask:
        return questions                             # 反问，等下一轮
    elif status == ready:
        brief = session.freeze()                     # 冻结 brief
        task = asyncio.create_task(orchestrator.run(brief))   # ← 在这里 spawn 编排器（后台任务）
        async for event in task:
            await session_service.on_event(event)    # 喂回 session + SSE
            yield sse(event)
```

> **两个 state**：SessionState（brief_draft / clarification_history / clarify 状态，归 session_service，存 sessions/briefs 表）；PipelineState（section_plans / evidence / claims / metrics / ...，归 orchestrator，由冻结 brief 初始化，存 phase_snapshots）。brief_draft 不是 pipeline state 的字段；冻结 brief 是 pipeline 的输入。

### 持久化生命周期

- 业务真相 + phase 级快照 → PostgreSQL（永久、不可重建，从 phase 边界恢复；恢复时**同 phase 取最新一行**，因 rework 会重复进入同 phase，多行快照按 created_at 区分）。
- **V1 单进程部署**（`uvicorn --workers 1`）：SSE 事件总线 = 进程内 `dict[session_id → asyncio.Queue]`；取消标志 = 进程内 `dict[session_id → bool]`。
- **V2 多进程/多实例**（扩展时）：事件总线用 Redis pub/sub（EventSink=publish / SSE=subscribe）；取消标志用 Redis。
- 审计历史 → V2。

> orchestrator（clarify + pipeline）作为**后台 asyncio 任务**运行（POST /research 时 `asyncio.create_task` spawn），不阻塞单个 HTTP 请求；SSE 连接从进程内事件总线订阅进度。重启后客户端重连 → 从 phase_snapshots 恢复 pipeline → 重新流事件（事件流是瞬态，真相在 snapshot）。

### 数据库设计（PostgreSQL，按需求分 schema）

| schema | 表 | 关键列 |
|---|---|---|
| user | users | user_id(PK)、email(unique)、password_hash、created_at |
| session | sessions | session_id(PK)、user_id(FK)、status、created_at、updated_at |
| session | messages | message_id、session_id(FK)、role、content、created_at |
| research | briefs | brief_id、session_id(FK)、task_type、brief(JSONB 10字段)、version、frozen_at |
| research | reports | report_id、session_id(FK)、version、content、created_at |
| snapshot | phase_snapshots | snapshot_id、session_id(FK)、phase、state(JSONB 完整 ResearchState)、created_at |
| audit | audit_log | session_id、ts、event_type、payload(JSONB)（V2） |

**决策**：细粒度对象（claims / evidence / comparable_metrics / analysis_artifacts / draft_sections /
critic_feedback）不单独建表，活在 `phase_snapshots.state` 的 JSONB 里——它们是随迁移演进的「演变态」、
以整体读写为主；需按单条查询/审计时 V2 再拆细表。只有「稳定、要按字段查询」的概念才建真实列。

### 认证（注册/登录）

- 认证不是领域概念：不设 domain 端口。验证 token → user_id 是 FastAPI 依赖（interface 层），user_id 作为参数传入 application。
- register / login 是 interface 的薄端点（`router/auth.py`），直接读写 `storage/models/user.py`（users 表）。
- 密码 bcrypt/argon2 哈希；JWT secret 走环境变量（章程 III）。
- SSE 端点用 cookie（EventSource 不能带自定义 header）；其余 API 用 `Authorization: Bearer`。

### 本地知识库（RAG）

- 第三检索源（local_search）：用户上传的论文 / 笔记 / 实验记录 / 数据集文档。
- 入库流水线：parse（MinerU 2.5）→ chunk（语义切分，表格/公式独立单元）→ embed（BGE-M3 dense+sparse）→ store（Milvus hybrid）→ 检索 rerank（BGE-reranker-v2-m3）。入库用 `asyncio.to_thread`（CPU 阻塞，不卡事件循环）；进度持久化到 documents 表（轮询读）；启动时扫 processing → 标 failed（崩溃恢复）。
- 核心原则「语义锚 + 原文分离」：嵌入语义锚（text=title+section、table=caption+列头+表注、formula=引入句+解释句），原文只存不 embed。详见 research.md 决策 #9。
- **存储归属**：PostgreSQL = chunk/文档元数据真相源（chunks 表含 object_key）；Milvus = 向量 + 去规范化元数据子集（kb_id/paper_id，供过滤）；MinIO = 原始内容（PDF + chunk 文本）。`chunk_id → object_key` 映射在 PG。删除顺序 PG → Milvus → MinIO。

## Constitution Check

*GATE: 已通过。设计本身编码了章程五原则，无违规。*

| 章程原则 | 设计如何满足 |
|---|---|
| I. 真实性与可溯源（不可协商） | `Claim→Evidence→SourceRecord` 全链 provenance；每条结论绑定带定位证据；Critic 逐项复核 |
| II. 测试先行（质量门） | pytest；合并前必须通过测试；machine/state 纯函数可单测 |
| III. 安全与数据保护 | 密钥走环境变量/配置，禁止硬编码；不上传私有基金申请数据 |
| IV. 成果保护与可回溯 | PostgreSQL 业务快照 + 幂等写入，支持从 phase 边界恢复 |
| V. 可部署工程化 | 模块化纯工人 Agent、Docker 部署、可复现 |

## Project Structure

### Documentation (this feature)

```text
specs/001-deep-research-agent/
├── plan.md              # 本文件
├── research.md          # Phase 0 输出（技术选型决策）
├── data-model.md        # Phase 1 输出（数据模型）
├── quickstart.md        # Phase 1 输出（端到端验证指南）
├── contracts/           # Phase 1 输出（接口契约）
└── tasks.md             # Phase 2 输出（由 /speckit-tasks 生成）
```

### Source Code (repository root)

```text
backend/
├── interface/                  # 接口层（对外，只往下调用）
│   ├── router/                 #   FastAPI 路由
│   │   ├── auth.py             #     /auth/register、/auth/login
│   │   ├── research.py         #     /research（提交、消息、SSE、报告、恢复）
│   │   └── knowledge_base.py   #     /knowledge-base/documents（上传、列表、删除）
│   └── dto/                    #   DTO（Pydantic 请求/响应模型）
│       ├── auth.py             #     RegisterRequest / LoginRequest / TokenResponse
│       ├── research.py         #     ResearchRequest / ClarifyResponse / BriefResponse / ReportResponse
│       └── knowledge_base.py   #     DocumentUpload / DocumentListItem / DocumentListResponse
│
├── application/                # 应用层（用例/编排，唯一控制流）
│   ├── ports.py                #   端口（application 消费的抽象）：StateStorePort / CancellationPort
│   ├── bootstrap.py             #   组合根：读配置 → 构造 adapters → 注入 agents/orchestrator → 返回 ResearchService
│   ├── orchestrator.py          #   pipeline 循环（autonomous）：machine.next_phase + advance + SSE + 取消 + 快照 + drain
│   ├── research_service.py     #   "发起一次深度研究"用例
│   ├── session_service.py       #   会话：对话历史 + clarify 循环（interactive）+ 串 pipeline 进度
│   ├── knowledge_base_service.py  #   本地知识库：文档入库（parse→chunk→embed→store）+ 列表/删除
│   └── sse.py                  #   领域事件 → SSE 序列化（data: {...}\n\n）
│
├── domain/                     # 领域层（核心资产，纯逻辑，不碰外部）
│   ├── ports.py                #   端口（domain 定义的抽象接口）
│   │                           #     LLMPort / SearchPort / RetrievalPort / EmbeddingPort / VectorStorePort / RerankPort / ContentStorePort / FetchPort / EventSink / CodeExecutionPort
│   │                           #     （读写不对称：读走 RetrievalPort[embed→hybrid→rerank]，写 ingest 直接用 Embedding+Vector；Rerank 仅读，internal top_k 走配置）
│   └── research/               #   研究领域（唯一领域）
│       ├── state.py            #     SSOT：ResearchBrief + ResearchState + Claim/Evidence/ComparableMetric + 控制字段 vs 产出字段
│       ├── machine.py          #     pipeline 状态机（纯函数）：WORKERS + next_phase + _route_after_review
│       ├── events.py           #     事件模型 + PHASE_DISPLAY 单源映射（PhaseEvent/StepEvent）
│       └── agents/             #     纯工人（注入 llm port + emit，只交结果）
│           ├── base.py         #       call_llm / parse_json
│           ├── architect.py    #       clarify()（判断+生成问题）+ plan()
│           ├── scout.py        #       research() / supplementary_research()
│           ├── data_analyst.py #       analyze()
│           ├── code_crafter.py #       analyze()（分析执行/图表）
│           ├── writer.py       #       write_report() / revise_report()
│           └── critic.py       #       review() → 只写 verdict/issues，不写 phase
│
└── infrastructure/             # 基础设施层（端口实现 = 适配器）
    ├── llm/
    │   └── deepseek.py         #   LLMPort 实现（Anthropic 兼容 API）
    ├── search/
    │   ├── bocha.py            #   SearchPort 实现（网页）
    │   └── arxiv.py            #   SearchPort 实现（论文）
    ├── vector/
    │   └── milvus.py           #   VectorStorePort 实现（本地知识库）
    ├── embedding/
    │   └── bge_m3.py           #   EmbeddingPort 实现（本地 sentence-transformer，dense+sparse）
    ├── storage/
    │   ├── models/             #   数据库模型（DDL / ORM），按需求分文件
    │   │   ├── user.py         #     users
    │   │   ├── session.py      #     sessions / messages
    │   │   ├── research.py     #     briefs / reports
    │   │   ├── snapshot.py     #     phase_snapshots（恢复用）
    │   │   └── audit.py        #     audit_log（V2 占位）
    │   ├── postgres.py         #   StateStorePort 实现（asyncpg + Alembic 迁移）
    │   └── redis.py            #   CancellationPort 实现（V2）
    ├── parser/
    │   └── pdf.py              #   文档解析
    └── sandbox/
        └── docker.py           #   CodeExecutionPort 实现：一次性容器 + --network=none / --read-only / --cap-drop=ALL / 资源上限

backend/tests/
├── contract/
├── integration/
└── unit/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/
```

**Structure Decision**: 四层整洁架构。领域逻辑（state/machine/agents）在 domain；控制流
（orchestrator）在 application；HTTP 翻译在 interface；实现（adapters）在 infrastructure。
Agent 不自己看 phase 决定做不做，转移规则集中在 machine.py。

## Complexity Tracking

> 无违规，无需填写。
