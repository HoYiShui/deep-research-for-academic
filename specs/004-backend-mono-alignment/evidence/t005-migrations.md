# T005：真实 PostgreSQL 迁移与数据保留

日期：2026-10-05；基于 `616c590`。依赖为 Compose PostgreSQL 16，非 fake。没有调用模型/搜索。

## 实现与设计依据

MODEL §1–3/§6、API §7、OPS §3：

- `backend/infrastructure/storage/migrations/0002_mono_research.sql`：UUID users/sessions/messages/Run/Checkpoint/Report，严格状态/phase、版本/seq、FK/Unique/Check、owner NOT NULL、冻结 hash 身份、初始/最新 Checkpoint 引用、租约/终态字段、tool_calls、幂等请求。
- Session/Brief/Run/Checkpoint 循环引用使用必要的 deferred FK；当前版本与 latest seq 必须在提交时存在。确认人必须是 Session owner，Run 必须引用匹配 hash 的冻结 Brief。Checkpoint 输入 hash/version/config 不可改变；完整 state hash 的计算/验证仍由 typed Repository 负责。
- Frozen Brief、Messages、Checkpoint、Report 的既有事实拒绝更新/删除。原文/报告不是 cascade 删除目标。
- 旧六表原样重命名为 public.legacy_*，加入只读 trigger，并在 `legacy_migration_records` 逐条记 mapped/isolated 和原因。没有删除旧行，没有将无 owner 的记录挂到开发身份。
- 只有 UUID/规范 email 均无歧义、字段合法的 User 可映射；原 email/ID/hash/时间仍保留在 legacy_users。规范 email/UUID 冲突不合并。旧会话缺少原始确认/可靠冻结/Run/seq 事实，统一保留为明确隔离记录，不猜造目标状态。已有 bcrypt hash 原样保留；T053 负责正式登录兼容/升级，而不是在迁移中编造密码。
- `migrations.py` 用同一连接/事务的 PG advisory lock 覆盖 schema_migrations 创建、检查、DDL 和版本记录；全部待执行批次原子提交，失败/cancellation/进程退出释放锁。未知或更高 schema 拒绝旧 runtime。
- 非空旧库升级必须传显式 `backup_dir`：锁住受影响六表的写入，先生成完整行+列/约束/索引元数据的本地 JSONL 逻辑备份；文件0600、目录0700、fsync、读回 SHA-256 校验和 checksum，再执行升级。失败备份不执行重命名。此备份仅是迁移受影响表的保护，不声称替代 T058 的 PG+MinIO 系统备份/恢复。
- `PostgresStateStore` 是尚未迁移的旧入口，固定 through_version=0001，失败关闭 pool；不能隐式升级用户库，也不能操作已经升级的 schema。
- `.gitignore` 忽略 `backend/.local/`，为后续组合根的本地备份落点保留安全边界；本轮备份仅在 pytest 临时目录。

## 先失败再实现

先写真实测试，空库反例失败：旧迁移不存在 research_runs/tool_calls/idempotency_requests。实现后重跑，逐步补充数据保留、进程竞争、备份失败和约束反例。

## 实际验证

`backend/tests/conftest.py` 的 pg_database 使用配置凭据连接 PG，只创建 `dr4a_test_<本轮随机UUID>`；所有 SQL 指向该独立库。关闭池后只删除本次明确创建的单一库名，没有清空配置库，也没有 wildcard 清理。

`cd backend && uv run pytest -q tests/integration/test_mono_migrations.py` 覆盖 11 项：

1. 空库 UUID schema、版本记录及重复执行。
2. 旧六表全字段逐行保留；完整备份内容逐行与迁移前比较，读回 hash/权限检查；无效 User/所有旧 Session 明确隔离。
3. 注入后续 SQL 除零：DDL、schema version 与旧行全部回滚，修复后可以重跑。
4. 两个独立 Python 进程同时迁移空库，均退出0，只有两条 version 记录，无 schema 创建竞态。
5. 非空旧库无备份目录时明确拒绝，旧数据/version 不变。
6. UUID FK、非空 owner、status/phase、lease 配对、单 Session 唯一 Run、单 Run seq、消息 seq、幂等 key 与冻结/追加事实不可变。
7. 当前 Brief/latest Checkpoint 不存在时提交失败，seq 仍1。
8. 规范 email 冲突两行都保留且隔离，不合并身份；旧 runtime 拒绝新 schema。
9. 备份目标无法创建时不升级，不丢数据。
10. Report/Session/Run 跨资源 FK、Report唯一/不可变、tool call去重和 succeeded 缓存字段。
11. Checkpoint 偷换 brief hash/version/config 被 PG 拒绝。

独立进程实测库示例：`dr4a_test_006ebf4fbc484585a3e29a2d9f33558c`，已删除。其他本轮测试库也逐个关闭/删除；只清理本轮创建的测试资源。测试使用 synthetic query/report/hash，不含用户私有资料。

- 全量 `uv run pytest -q`：**201 passed，4.80s**，其中上述11项用真实 PG。
- 修改 Python 文件 Ruff：提交前 All checks passed。
- `git diff --check`：通过。
- 配置库只读核对：`schema_migrations=[0001_init]`、sessions.session_id 类型 `text`；用户库未升级到0002。没有读取/输出密钥或用户正文。

## 未完成边界

T005 完成只证明迁移与约束，不能替代 T006 的真实 Repository CAS/幂等/冻结故障注入、T007 HTTP 或完整业务 E2E。阶段0事务门仍等待 T006。

本轮未修改用户 `.env`，未提交 `docs/implementation/`，未推送。
