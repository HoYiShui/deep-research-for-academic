# T015：取消、失败与显式恢复（部分完成）

日期：2026-10-06。真实 PostgreSQL 的隔离 `dr4a_test_<uuid>` 数据库；不迁移用户配置数据库，不使用模型输出替代事务验证。

实现：`run_termination.py` 在同一短事务中锁定 Session → Run，提交两者的状态、Failure、租约释放和完成时间。取消未冻结会话不创建 Run；取消 ready/running 先持久化 cancelling；当前持租执行器安全停止，或恢复扫描发现无有效租约后提交 cancelled。取消不能被迟到失败覆盖。

扫描：过期 running → failed/interrupted；超过 30 分钟的 ready → failed/queue_timeout；只允许显式 resume，不自动领取失败 Run。resume 校验最新 seq、原配置与快照身份，保留 Run ID、Checkpoint、预算、attempt；再次领取才递增 attempt/token。等待超时从 Session 最近 ready 转换时间计算，恢复后不会立刻因原始创建时间再次超时。

容量：freeze/resume 以 owner User 的 NO KEY UPDATE 锁串行限制 ready 队列，兼容同事务新建 Session 的外键 KEY SHARE，避免首次 CLI 冻结的锁升级死锁。HTTP/CLI 冻结路径传递 Settings.owner_queue_limit；claim 的全局/owner 活跃容量逻辑不变。

反例：owner 404；重复取消不增 revision；取消后不领取；活租约 cancelling 不被扫描提前停止；停止后旧 token 拒写；失租不自动重跑；恢复不清预算；旧 seq/配置变化/并发第二次恢复拒绝；Session 终态写失败时 Run 状态回滚；并发冻结只有一项占队列且失败项未半冻结；两恢复器只提交一次；租约在终态事务内过期仍拒写。

Fake 同步提供上述接口，仅用于契约回归，不作为真实 PG 证据。

验证：本批新增前 11 项反例后，全量 `uv run pytest -q`：325 passed，52.00s。再补首次 CLI FK 并发锁及终态事务内失租两项后，`test_mono_run_lifecycle.py`：27 passed，5.66s（这两项未计入前述全量 325）；Ruff 格式/检查通过。所有测试数据库由 fixture 精确清理。

尚未完成：报告/done 原子发布、真实 TaskRunner 自动扫描/执行、HTTP cancel/resume、SSE。T014/T015 保持未勾选。配置可用性（模型/索引版本真实存在）需组合根后续校验；本批只证明原配置不被静默替换。
