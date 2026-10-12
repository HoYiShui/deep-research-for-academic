# case1 检索与选源验收（2026-10-11）

## 运行与结果

- 分支：`fix/research-api-contract-alignment`。
- 运行目录：`backend/.local/lab/task1-20261011-v8/1011-2047-case1/`。
- 先把 `backend/.local/lab/task1-20261011-v3/1011-160453-case1/states/003-to-research.json` 复制为本次目录的 `state.json`，再从 `backend/` 执行 `PAPER_SEARCH_PROVIDER=both uv run python -m lab.run --state .local/lab/task1-20261011-v8/1011-2047-case1/state.json --stop-after research`。`both` 分别计量 OpenAlex 与 arXiv，web 经 `search-router` 的 `body` 路由。运行后的 `state.json` 已推进到 `analyze`，不能直接重复这条 Research 命令。
- Research 完成后进入 `analyze`，`error.txt` 不存在；state 有 **5 个来源、45 条 Evidence、51 条 Claim、52 条 ClaimEvidenceLink**。累计预算为 **6 次 LLM、49 次 search、17 次 fetch、49,046 tokens、202 秒**，包含所复用 plan 快照的预算。
- 5 个来源均对应 gold 列出的关键文献；`emergentmind.com` 等 AI/论文聚合页 **0 个**。

| gold 关键文献 | 本次实际取得的原文 URL |
|---|---|
| Glasser & Lindauer 2013 | `https://www.ieee-security.org/TC/SPW2013/papers/data/5017a098.pdf` |
| Tuor 2017 | `https://fw.cs.wwu.edu/~hutchib2/papers/aics17_insiderthreat.pdf` |
| Le & Zincir-Heywood | `https://web.cs.dal.ca/~lcd/pubs/TNSM2020.pdf` |
| Yuan 2019 时序点过程 | `https://arxiv.org/pdf/1910.03171v2` |
| Jurišić & Tomičić 2026 综述 | `https://www.fvv.um.si/rv/arhiv/2026/2026-02-Jurisic-Tomicic.pdf` |

## 诊断边界

- trace 中有 10 次 `fetch_unavailable`、2 次 `fetch_http_error`；OpenAlex 有 10 次 `search_unavailable`、8 次 `search_http_error`、3 次 `search_rate_limited`，arXiv 有 3 次 `search_unavailable`。这些是外部检索/抓取依赖的降级，未触发 lab 硬门或预算上限。
- trace 有 77 条 `extraction_repair`。它们属于下一项“抽取与 Claim 聚合”的诊断，不能把本次 Research 通过误记为抽取质量达标。
- 本次仍未取得 KiltHub 官方数据集页与 Log2vec 原文；5/7 关键来源满足本阶段门槛，但不等于最终报告质量或 Analyze 验收。
- 对照基线的 302 条 Claim，本次为 51 条；同一 subject/predicate 的聚合与 supported/limited 比例仍须按下一项单独验证。

## 最终代码复跑（2026-10-11 20:49）

- 运行目录：`backend/.local/lab/task1-20261011-v9/1011-2050-case1/`；同样从 v3 的 `003-to-research.json` 快照启动，执行 `PAPER_SEARCH_PROVIDER=both uv run python -m lab.run --state .local/lab/task1-20261011-v9/1011-2050-case1/state.json --stop-after research`。该次已包含 fetch 的安全连接重试实现。
- Research 完成后转入 `analyze`，无 `error.txt`：**9 个来源、72 条 Evidence、85 条 Claim、91 条 ClaimEvidenceLink**；累计预算 **13 次 LLM、48 次 search、17 次 fetch、79,755 tokens、260 秒**。trace 中有 8 次 `fetch_connect_retry`，表明重试路径在真实下载中得到执行。
- 命中 gold 的 **4 类关键文献**：Glasser & Lindauer 2013（IEEE 原文 PDF）、Tuor 2017（AAAI 页面、arXiv 和作者 PDF）、Yuan 2019（arXiv PDF）、Jurišić & Tomičić 2026（期刊页和全文 PDF）。另取到 IEEE DataPort 的 TRACE-IT 页面。AI 聚合页 **0 个**。
- 9 个来源不是 9 篇独立文献：Tuor 的多个入口和综述的 HTML/PDF 均被保存。当前去重覆盖规范化 URL/DOI，同篇文献跨域且无共同 DOI 时仍可能重复；后续抽取和 claim 聚合需要处理这一点。
- trace 中有 22 次 `provider_failed`、8 次 `lab_tool_exception`，主要为 OpenAlex/arXiv 检索及原文下载的外部错误；另有 1 次 `model_validation_failed`（模型返回无效 JSON）和 77 次 `extraction_repair`。运行最终完成，未触发硬门或预算上限；这些抽取诊断留给任务 2 单独验收。
