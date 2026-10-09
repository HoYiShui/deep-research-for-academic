# 下一阶段任务：把 case1 报告质量拉近 gold

> 交接说明（2026-10-09）。新 session 先通读本文，再动手。

## 背景（必须先理解）

- 项目：DR4A，学术研究辅助报告。后端设计权威是 `docs/mono/` 五份文档。期望输出（gold）在 `docs/cases/case-{1,2,3}-*.md`，生成过程的 trace 在 `docs/cases/traces/02098d52.trace.md`。
- **当前策略：harness 冻结，先把 Agent 工作流做好。** 不要去做 lease、幂等、KB 删除、SSE 等 harness 任务（`specs/004-backend-mono-alignment/tasks.md` 里 US2/US5/US6 的剩余项），除非它们直接阻断真实运行。
- 真实运行入口是 **lab runner**：`backend/lab/run.py`。
  - 它复用正式的 agents、workers、merge、Machine 和 report serializer，但绕开 PG 账本、租约、预算预留这些 harness。
  - 每个单元的 state、完整的 prompt/response trace、真实异常栈都会落到 `backend/.local/lab/<run>/`。
- 2026-10-09 case1 首次真实产出报告：
  - 结果 `needs_more_work`，32 分钟，62 次 LLM 调用，约 1.6M tokens。
  - 基线报告存在 `backend/lab/baselines/case1-20261009-report.md`。
  - 对应的完整 state 在本机 `backend/.local/lab/1009-163616-case1/`（git-ignored，可能已不在）。
- 上一阶段修过的坑见 commit `6b1bcd9`、`8a83cdb`、`1ed5dba`、`19383aa` 的说明。总原则：**结构错误才失败，语义违规用代码降级并记 trace**。不要把它们改回硬门。

## 运行环境

```bash
cd backend
# 1) 依赖：docker 里的 PG/MinIO/Milvus 已在跑（docker ps 确认）；本阶段只用到 MinIO（原文存储）
# 2) Web 检索网关（Serper/Tavily），lab 默认用它：
(cd ~/Workspace/search-router && go build -o /tmp/dr4a-search-router ./cmd/search-router \
  && SEARCH_ROUTER_CONFIG=$PWD/config.yaml nohup /tmp/dr4a-search-router > /tmp/search-router.log 2>&1 &)
curl -s -X POST localhost:8080/search -H 'content-type: application/json' -d '{"query":"CERT insider threat"}' | head -c 200
# 3) 全量运行 / 续跑 / 单步
uv run python -m lab.run --brief lab/briefs/case1.json
uv run python -m lab.run --state .local/lab/<run>/state.json              # 从最后保存的 state 续跑
uv run python -m lab.run --state .local/lab/<run>/state.json --recompute-coverage
uv run python -m lab.run --brief lab/briefs/case1.json --stop-after research
# 4) 测试（约 5 分钟，必须保持全绿）
uv run pytest -q tests && uv run ruff check application domain infrastructure cli lab
```

注意事项：

- **Claude Code 环境变量**：Claude Code 会注入 `ANTHROPIC_BASE_URL` 和 `ANTHROPIC_AUTH_TOKEN`，它们会劫持后端的 DeepSeek 配置。lab 会自动丢弃这两个变量；其他入口要用 `env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN ...` 启动。
- **网络**：本机到 `export.arxiv.org` 不稳定；到 `api.openalex.org` 偶尔出现 ConnectError，适配器内部有重试。
- **成本**：一次完整的 case1 约 1.5M tokens。能续跑就不要重跑。
  - 只改 writer prompt 时：取 `states/` 下 research 完成后的快照（`NNN-to-write.json`），从 write 阶段续跑。
  - 只改序列化时：直接对 `states/999-done.json`（把 phase 改回 `review`、`final_report` 置为 null）调用 `domain.research.reporting.build_report`，零 LLM 成本。
- **调试方法**：失败时先看 `error.txt`，再在最新的 `trace-*.jsonl` 里找这些事件：
  - `model_validation_failed`
  - `extraction_repair` / `writer_repair`
  - `writer_context_selection`
  - `fact_reread_differs`
  - `lab_tool_exception`

  据此判断问题属于"硬门"、"上限"还是"模型能力"。

## 基线 vs gold 的差距（逐条有证据）

1. **证据底座薄**：12 条引用中有 AI 聚合页（emergentmind，重复收录两次）、硕士论文配置手册、只拿到标题页的付费综述。gold 中的关键来源缺失：
   - SEI/KiltHub 官方数据集页（DOI 10.1184/R1/12841247.v1）
   - Glasser & Lindauer 2013
   - Tuor 2017
   - Log2vec（CCS 2019）
   - Yuan 2019 时序点过程（TPP）
   - Le & Zincir-Heywood
   - 综述全文（Jurišić & Tomičić 2026）
2. **把已有工作当成缺口**：报告声称"CERT 上缺 TPP 评估"，但 Yuan 2019 恰恰做了这件事，只是没有检索到。
3. **hedge 过多**：第 2–4 章约一半篇幅是"尚未核实/本轮材料不代表…"。全文只有 3 条 factual 陈述，§2.2"已确认事实"为空。第 1 章复述 brief 时被整体标成"假设"。
4. **前后矛盾**：§3.3 首选"事件级溯源（候选问题一）"，§4.3 又说"候选问题二（跨版本）优先级最高"；§4.2 声称没有 TRACE-IT 材料，但前文已经引用了它。审阅没有拦下这些问题。
5. **渲染问题**：§3 把卡片和推荐用"；"拼接成段落，重复输出了一遍；有 `&gt;`/`&amp;` 转义残留；用了 64 位哈希锚点；相邻的同源引用没有合并；第 5 章风险清单约 23k 字（gold 是约 10 行的表格）。
6. **已知未修的问题**：
   - rework 的 `re_research` 不发新检索，只重算 coverage（见 `application/phase_units.py` 里 research 单元的生成逻辑）。
   - research 抽出 302 条 claim，过碎，需要聚合。
   - 表格观察值大量被丢弃，原因是行列标签不是原文（case1 无影响，case3 会受影响）。

## 任务（按优先级；每项先改、再用最低成本验证、再进入下一项）

### 1. 检索与选源（research query & selection）——影响最大

- **plan 层**：每个有子问题的章节，anchors 要覆盖三类：奠基或代表论文（作者+年份+方法名/题名）、官方或一手资料（数据集页/文档）、综述。改 `domain/research/agents/prompts.py` 中的 `PLAN_PROMPT_TEMPLATE`，可以参考 gold trace 里的 25 条真实 query。
- **候选选择**（`application/phase_workers.py` 的 `research_worker`）：
  - 按规范化 URL/DOI 去重，避免同一页面重复收录。
  - 降低 AI 聚合页/问答站/配置手册的优先级，例如加一个域名黑灰名单或来源打分。
  - 付费墙页面（抓取结果正文过短或只有预览）记为降级，并尝试 OpenAlex 的 OA 版本。
  - 每个 query 选 4 个的上限可以按 provider 调整。
- **rework 补查**：让 `re_research` 针对 ReworkTarget 涉及的 ClaimSpec 生成定向 gap query，执行真实检索。可以复用 `gap_queries_per_spec` 预算。这是 dataflow §3.3 规定的行为。
- **验收**：重跑 case1 的 research（`--stop-after research`），对比 sources 列表。gold 关键来源中至少命中 4 个，AI 聚合页为 0。

### 2. 抽取与 claim 聚合（extraction）

- 让数据集事实（版本、用户数、内鬼数、场景数、标签粒度、文件结构）和方法事实（名称、年份、venue、核心机制）优先被抽成可登记的 factual claim。
- 同一 subject/predicate 的近似 claim 要合并，或者在抽取时复用已有 claim。目标是 claim 数量下降一个数量级，同时 supported/limited 比例上升。
- 抽取 prompt 位于 `prompts.py` 的 `EXTRACTION_PROMPT_TEMPLATE`。逐条丢弃的机制保留。

### 3. 写作风格（writer prompt）——可从 write 阶段续跑验证

- 改为"事实先行，局部标注待核实"：
  - 禁止段落级免责模板。
  - 每段至少包含一个可核实的具体项（数字、方法名、版本、来源）。
  - 第 1 章复述 brief 时用 limitation/recommendation 等合适类型，不要标成 hypothesis。
- 相关文件：`WRITE_PROMPT_TEMPLATE` / `WRITE_FEW_SHOTS`。few-shot 可以从 gold 中摘取。

### 4. 审阅（critic）

- 增加跨章节一致性检查：推荐只能有一个，卡片编号在第 3/4 章保持一致，"某材料不存在"的说法不能与已引用的内容冲突。
- 增加检查：声称的研究缺口不能与已引文献或已有 claim 矛盾。
- 这些检查优先用确定性代码实现（例如比对 task_payload 的推荐与第 4 章 recommendation 语句）。只有确定性做不到的部分才放进 `REVIEW_PROMPT_TEMPLATE`。

### 5. 报告序列化（`domain/research/reporting.py`）——零 LLM 成本验证

- 去掉第 3 章里 task_payload 行的重复渲染（由 `writer.materialize_chapter` 注册的"；"拼接 statement 在投影时出现两次）。
- 修复 HTML 转义残留。
- 锚点改为短 ID（例如 `s3-07`）。
- 相邻且同一来源的引用合并。
- 第 5 章风险清单改为表格（风险 | 证据状态 | 影响 | 验证方式），按 ClaimSpec 和 critic issue 聚合，控制在约 10–20 行。
- 对照 `docs/mono/data-model.md` §4.5 的 serializer 约定，必要时同步修改该文档。

### 6. 扩展到 case2/case3

case1 达标后，用 `lab/briefs/case2.json`、`case3.json` 各跑一次，记录新暴露的问题。case3 是 evaluation_design，会用到 analyze 阶段和表格观察值。

## 验收与工作纪律

- 每完成一项：全量测试保持绿；有真实运行证据（run 目录名 + 关键数字）；按主题提交到当前分支并推送（commit 末尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`）。
- 修改测试时，只改那些断言旧的"硬拒绝"语义的用例，并在用例里写明新语义。不要为了通过测试而删除覆盖。
- 设计与代码冲突时，以 `docs/mono/` 为准；如果确实要改设计，同步修改受影响的 mono 文档。
- 质量对比可以派一个子 agent 去读 gold 和新报告，逐章打分。重点看：证据是否覆盖 gold 关键来源、事实密度、hedge 比例、前后一致性、篇幅。
- 不要编造引用。gold 本身也有归因错误（见 `docs/cases/case-1-idea-exploration.md` 末尾的「审查评注」），不能把 gold 当作事实来源照抄。
