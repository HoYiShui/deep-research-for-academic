# T006：真实 PG Repository 与原子冻结

日期：2026-10-05；基于 `8e7510b`。以下为真实 PostgreSQL 存储验证，不是 HTTP/真实模型 E2E。

## 实现及依据

ARCH §3–5、MODEL §2–3/§6、API §6–7、OPS §3：

- `backend/infrastructure/storage/research_postgres.py` 同目录拆分出 PostgresResearchStore/UoW、User/Research/Request Repository。组合根传入已迁移 pool；Store 不隐式升级用户库，也不自持第二事实库。
- 全部写方法使用同一 opaque transaction；无内部 commit。handle 校验同 Store/活跃状态；退出后失效。PG 错误在回滚后转换为安全 AppError/AdapterError，不暴露原 SQL/私人字段；读出的损坏记录报 schema_incompatible，而不是返回空成功。
- 资源读使用 owner WHERE/JOIN；Session/Brief/Message/Run/Checkpoint 都返回严格类型，JSONB 在边界反序列化并验证 hash/结构。
- Session 初始 insert 与 Brief/Message 共事务；后续行锁+expected revision CAS，明确增 brief_version；拒绝改原 query/owner/created_at 或更新已冻结状态。Message 只允许连续追加 sequence。
- Freeze 锁 Session，检查 revision/version/status/候选内容与来源，冻结已有 Brief，插唯一 Run/seq1 Checkpoint/confirmation messages，再变更 Session ready，全部同事务。Request.complete 可以在同 Tx 缓存成功响应；任一故障回滚全部事实。
- 幂等 reserve 用唯一键+ON CONFLICT+行锁；同 body completed 重放、异 body 冲突、处理中 Retry-After。使用 PG clock_timestamp 的120s操作租约；renew/complete/release 比较原 hash+到期值并验证未过期，迟到 holder 不可提交。503/5xx 不允许存 completed，须释放以重试。成功响应保留策略由后续扫描/清理处理，目前没有过早删除。
- `application/records.py` 补首个 Checkpoint 空输出/零预算/无既有审核返工的 guard；KB冻结版本 allowlist 可保留，执行成果不能混入新Run。fake 补同样的 brief版本/不可变 query/临时失败不可缓存政策，不拿 fake 验收替代 PG。

## 先失败再实现

`test_mono_transactions.py` 先失败于不存在 PostgresResearchStore，再实现，逐步补失败与竞争反例。

## 测试与结果

`backend/tests/integration/test_mono_transactions.py` 的10项真实 PG 测试（加1项 aggregate 单测）覆盖：

1. Session/Brief/幂等记录共享事务回滚；typed 读取、owner 隔离、email 规范查询。
2. Freeze+响应缓存注入提交前异常：仍 confirm、未冻结、无 Run/Checkpoint、请求仍处理中；重试成功后同202/同Run重放。
3. 两个实际连接竞争 Session revision 和 request reservation，只有一个胜者。
4–5. 在冻结 Brief 后的 Run insert、Run 后的 Checkpoint insert 注入**真实 SELECT 1/0 SQL 错误**，回滚到原 confirm；不存在半冻结成果。
6. PG 时间的续租、过期重新领取、旧 holder 拒写、释放重试、已完成异 body 冲突、503拒缓存。
7. foreign/closed Tx handle 拒绝。
8. 同 Session 两个确认候选竞争，只创建1 Run/1 Checkpoint。
9. 冻结途中实际 task.cancel()，上下文回滚，仍 confirm/无Run/无Checkpoint。此项不是SIGKILL恢复验收，后者在T022/T055。
10. typed Message 连续读取及跨 owner 不可见；不可修改原 query。
11. 首个快照夹带 phantom LLM usage 时在写库前拒绝（确定性 aggregate 测试）。

命令：

- `cd backend && uv run pytest -q tests/integration/test_mono_transactions.py`：真实存储反例通过。
- 全量 `uv run pytest -q`：**212 passed，12.17s**（包含T005的11项真实迁移测试及本轮10项真实事务测试）。
- 修改文件 Ruff：**All checks passed**。
- `git diff --check`：通过。

每项使用 conftest 的 `dr4a_test_<UUID>` 独立数据库，完成后只关闭/删除本项明确创建的测试库。未写配置的用户数据库，未调用收费模型或搜索，未改 `.env`，未提交 `docs/implementation/`，未推送。

## 边界

阶段0的真实 PG 迁移/事务门已有存储证据；T007 HTTP错误/身份装配尚未完成，不能称 API 后端已对齐。真正初始Clarify、多轮确认与持久恢复仍由 T008–T013 完成。

该 Repository 的 Run 生命周期仅涵盖原子创建/只读；claim/lease/checkpoint 递增/报告发布/取消/resume 在 T015，KB范围冻结在 T050。不能由当前创建测试推断完整 Pipeline 或 KB 已可用。
