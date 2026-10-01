# Quickstart：端到端验证指南

> Phase 1 输出。用最小可运行路径验证 feature 工作。不含完整实现（实现细节在 tasks.md）。

## 前置

- Python 3.11+、Docker（用于 Redis / PostgreSQL / Milvus）
- LLM 密钥（环境变量，禁止硬编码）

## 启动

```bash
# 1. 启动依赖
docker compose up -d          # redis / postgres / milvus

# 2. 安装后端
cd backend && pip install -e ".[dev]"

# 3. 起后端（含 SSE）
uvicorn src.api.main:app --reload

# 4. 起前端
cd frontend && npm run dev
```

## 最小验证闭环（跑通一个任务类型）

用 `docs/cases/case-1-idea-exploration.md` 的 query 验证：

1. `POST /research` 提交 query（`task_type=idea_exploration`）→ 拿到 `session_id`。
2. 通过 SSE 观察：`phase=clarify` → 澄清（若触发）→ `ready` 冻结 ResearchBrief。
3. 观察 `research` 阶段：DeepScout 多源检索、证据链回链。
4. 观察 `review` 阶段：Critic 逐项复核、缺口回流。
5. `GET /research/{id}/report` 拿到最终报告。

## 验收标准（对照 spec 的 Success Criteria）

- **SC-002 可溯源**：报告里每条结论能回链到带定位的证据（抽查一条，应能定位到论文+页码/表格）。
- **SC-003 口径**：跨研究比较处标注可比性，口径不一致时标记不可比。
- **SC-004 覆盖**：ResearchBrief 规定的关键论断在报告中有覆盖，未覆盖处显式标注证据不足。
- **SC-006 恢复**：任务执行中途杀掉进程，重启后从最近 checkpoint 继续，不重复已完成的检索。

## 对照 gold 案例

系统产出应与 `docs/cases/case-1-idea-exploration.md` 的完整报告对照，定位差距。差距本身即下一步调优/改 prompt/改设计的依据（见 `docs/cases/` 的审查评注）。
