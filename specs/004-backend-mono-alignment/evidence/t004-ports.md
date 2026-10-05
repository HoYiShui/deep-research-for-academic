# T004：共享事务与 typed 契约

日期：2026-10-05；基于 `61860b0`。本轮仅确定性测试，不是 PG 原子性验收。

## 实现路径

- `backend/application/ports.py`：UnitOfWorkPort/TransactionPort、UserRepositoryPort、ResearchRepositoryPort、RequestStorePort、SessionServicePort。本阶段只定义创建/澄清/冻结/读取所需操作；Run 租约/交付操作在 T015 扩展。事务通过 opaque handle 跨 Repository 传递，App 不持连接/SDK，不允许每个写方法自提交。
- `backend/application/records.py`：User、SessionChange、SessionInput、ValidatedFrozenInput、FreezeCommit、IdempotencyRecord。冻结 aggregate 校验 Session/Brief/Run/Checkpoint 的身份、版本、hash、来源、初始状态、确认归属。User email trim+lower；开发用户无密码，不允许普通用户缺 hash。
- `backend/application/errors.py`：AppError；`backend/domain/ports.py`：AdapterError 和 ClockPort。业务错误不依赖 HTTPException。
- `backend/infrastructure/clock.py`：SystemClock，UTC 记录时间与 monotonic timeout 分离；PG lease 的时钟仍需数据库提供，不能用该 wall clock 替代。
- `backend/infrastructure/fake_research.py`（经 fake.py 导出）：共享 staged snapshot 的 FakeResearchDatabase、三个 fake Repository、FakeClock。写入必须传同库活跃 Tx，成功退出统一 commit，异常/cancellation 回滚，读取返回副本；仅用于受控装配，未注入生产 bootstrap。
- 既有 StateStorePort/旧 fake 保留为明确的 pre-mono 接口；T011/T017/T042 等迁移完成后移除。此文件不宣称旧进程内取消已满足持久取消。

设计来源：ARCH §3–5、MODEL §1–3/§6、API §6–7、OPS §3。

## 先失败再实现与验收

新反例先运行：因 AppError/新 Port 不存在而 collection 失败，然后实现。为保持旧 contract 回归，新的验收拆到 `backend/tests/contract/test_mono_ports.py`，而不是混称旧 test_ports 的 fake 已满足新设计。

8 项 contract 测试证明：

1. User 与 Session Repository 共用一个 Tx；注入异常两者都回滚，正常退出都提交；非 owner 不可见。
2. revision CAS 拒绝旧候选。
3. foreign/closed transaction handle 拒绝写入。
4. wall clock 回拨不影响 monotonic deadline。
5. Freeze 与成功响应缓存同事务；注入失败不留下冻结 Brief/Run/Checkpoint/ready 状态；正常提交后重放同202响应及同Run；跨owner不可读Checkpoint。
6. 幂等 key 绑定不同请求冲突；有效操作租约处理中拒绝；过期后重新 reserve，旧 holder 不可完成；释放允许重试。
7. 两个候选竞争仅一个 revision CAS 胜出。
8. 混合会话身份、错误revision、已执行Run、错确认人/初始seq的 FreezeCommit 在入库前拒绝。

验证命令：

- `cd backend && uv run pytest -q` → **190 passed，2.86s**。
- 修改文件 `uv run ruff check ...` → **All checks passed**。
- `git diff --check` → 提交前检查通过。

## 边界与下一步

fake 用一把内存锁，不能证明跨进程锁、PG FK/CAS、迁移或 SQL 失败回滚。T005/T006 必须独立通过真实 PG 测试；阶段0门仍未通过。没有调用 LLM，也没有向用户数据库写入。

已只读核对 Compose：五个中间件均 running/healthy。旧 `0001_init.sql` 使用 text ID、可空 owner、bigserial 消息/快照，不能直接声称已符合新 UUID/版本/seq 设计；下一任务必须保留/隔离无法无损转换的旧记录。

本轮没有清理数据库/对象，没有改 `.env`，没有提交 `docs/implementation/`，没有推送。
