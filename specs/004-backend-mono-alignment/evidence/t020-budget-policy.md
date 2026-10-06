# T020 预算预留策略（部分）

`tool_budget.py` 实现纯预算门：已结算用量加尚未结算的预留，模型请求同时检查调用数及 token 上限；普通扩展不能花掉至少 8 次/12000 tokens 的终末收缩/审核预留。search/fetch 各有独立调用上限；deadline 与绝对超预算对全部工具生效。terminal 由代码指定且严格 bool，不能让模型字符串开启。

返回值是预留后的投影，不是 measured usage，也不会修改输入或持久 State。`used` 与 `pending` 必须无重叠；pending 不包含已经结算的调用、不重复记执行时间。后续 PG Adapter 必须在同一 Run 锁事务读取计数并插入预留，不能把此纯函数当并发锁。

2026-10-06：**12 passed in 0.04s**，覆盖并发前的 pending 用量计算、调用/token 双上限、终末预留、search/fetch 上限、非法类型、deadline、损坏账本与不修改输入。这里只证明策略，不证明多进程原子预算。

## 断连后的真实回归环境

仓库与未提交文件在 PSSD 重新挂载后恢复。Docker Desktop 重启后，原 PostgreSQL 容器仍反复退出，日志为 `PANIC: replication checkpoint has wrong magic 0 instead of 307747550`。没有重置 WAL、修改/删除原卷或重建原库。

为不阻断实现，创建单独测试容器 `dr4a-verify-pg-20261006-1041`（`postgres:16-alpine`，`--rm`、自动分配回环端口 63183）。这是空的隔离 PG，不是原库恢复。测试用进程级 DATABASE_URL 覆盖，不改 `backend/.env`；所有 fixture 仍只创建/清理 `dr4a_test_<uuid>` 数据库。MinIO 测试使用独有测试 bucket。

这一环境下最终完整回归 **452 passed in 56.22s**，验证此前磁盘断连中断的计量/身份实现以及预算策略（含严格 bool 反例）。原环境对应失败回归 **342 passed, 109 errors in 25.76s**，因 PG 不可连接，不记通过。

原库仍未恢复；隔离测试 PG 不替代开发/生产 readiness 验收。T020 的 PG ledger、成功调用恢复、uncertain 账本仍待实施。
