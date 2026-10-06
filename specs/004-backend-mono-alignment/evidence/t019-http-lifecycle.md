# T019：HTTP 取消、恢复、报告读取（部分完成）

2026-10-06，实际 ASGI HTTP 路由 + 隔离真实 PostgreSQL；受控 Clarify 模型，不以这批测试声称真实研究报告质量。

- POST cancel：冻结前/failed/cancelled 返回200 cancelled；ready/running/cancelling 返回202 cancelling。先授权，必须幂等键和严格空 body；状态与幂等响应缓存同事务提交。
- POST resume：严格正整数 checkpoint_seq。仅可恢复 failed、最新快照、无取消；复用原冻结配置，不替换模型/索引版本。202 返回原 Run ID；相同键重放，另一个键的重复恢复409。配置真实可用性由后续 executor 校验，不把“配置没改变”等同于“外部版本仍存在”。
- GET report：从 owner-scoped 已提交 Report 和 done Checkpoint 验证一致性后返回 Markdown/references/risks；保留真实换行，未发布409。completed之后取消409。

新增5项HTTP反例：冻结前取消重放不多调模型/不建Run；ready取消不假称已停/不产报告；失租后恢复重放保留Run/seq/attempt；受控报告读取与完成态；响应缓存写故障回滚取消、同键可安全重试。HTTP Clarify/生命周期27项全部通过，21.25s。

全量 `uv run pytest -q`：343 passed，50.58s；Ruff 与 git diff --check 通过。

SSE订阅、真实worker调度及配置可用性校验尚未完成，T019不勾选。报告测试对象是刻意披露证据不足的事务fixture，不是A6验收产物。
