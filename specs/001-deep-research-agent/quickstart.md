# Quickstart：端到端验证指南

> Phase 1 输出。用最小可运行路径验证 feature 工作。不含完整实现（实现细节在 tasks.md）。

## 前置

- Python 3.11+、Docker（PostgreSQL / Milvus / MinIO；沙箱镜像 `python:3.12-slim` 按需拉取）
- LLM 密钥（环境变量，禁止硬编码）；BGE-M3 嵌入模型（本地，或 embedding API）

## 启动

```bash
# 1. 启动依赖（postgres / milvus / minio）
docker compose up -d

# 2. 安装后端
cd backend && pip install -e ".[dev]"

# 3. 起后端（V1 单进程，含 SSE）
uvicorn src.api.main:app --reload   # 默认单 worker，即单进程

# 4. 起前端
cd frontend && npm run dev
```

## 最小验证闭环（跑通一个任务类型）

用 `docs/cases/case-1-idea-exploration.md` 的 query 验证：

1. **注册/登录**：`POST /auth/register` → `POST /auth/login` → 拿 token。
2. **建会话**：`POST /research`（query + `task_type=idea_exploration`）→ `{ session_id, status: "clarify" }`（不返回 sse_url）。
3. **澄清（交互式）**：`POST /research/{id}/messages` 逐轮回答澄清问题 → 直到 `{ status: "ready", sse_url }`。
4. **观察流水线**：`GET /research/{id}/events`（SSE）观察 `phase` 事件：`plan → research → analyze → write → review → done`。
5. **拿报告**：`GET /research/{id}/report`。

（注意：SSE 连接在 `ready` 之后才建立；`POST /messages` 返回 JSON，不返回 SSE。）

## 知识库（可选）

- `POST /knowledge-base/documents` 上传 PDF → `202` → 轮询 `GET /knowledge-base/documents/{doc_id}` 直到 `done`。
- `POST /knowledge-base/search` 检索验证。

## 验收标准（对照 spec 的 Success Criteria）

- **SC-002 可溯源**：报告里每条结论能回链到带定位的证据（抽查一条，定位到论文+页码/表格）。
- **SC-003 口径**：跨研究比较处标注可比性，口径不一致时标记不可比。
- **SC-004 覆盖**：ResearchBrief 规定的关键论断在报告中有覆盖，未覆盖处显式标注证据不足。
- **SC-006 恢复**：任务中途杀掉进程，重启后从 `phase_snapshots` **同 phase 取最新**恢复，不重复检索。

## 对照 gold 案例

系统产出应与 `docs/cases/case-1-idea-exploration.md` 的完整报告对照，定位差距。差距本身即下一步调优/改 prompt/改设计的依据（见 `docs/cases/` 的审查评注）。
