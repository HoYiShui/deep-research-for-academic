# T015：Report 与完成态的原子发布

2026-10-06。隔离真实 PG，受控的 `needs_more_work` 报告仅用于事务测试，不代表 cases 粒度的报告验收。

`publish_report` 锁 Session → Run，校验 worker/token/最新 seq/PG 租约、running 且没有取消请求；前序必须是已提交的同版 review。发布不得偷改审核过的事实、预算、正文或 Binding，完整五章与 Report 必须相同。Report、done 全量 Checkpoint、Run.completed/释放租约、Session.completed 同一事务提交。Reference 只能回链已提交 Source/Evidence。业务质量/正文固定骨架门仍由 T036/T037 完成，本存储接口不冒充研究质量审核器。

真实 PG：四处写入故障各自导致整个事务回滚；先取消不能发布；先发布之后取消 409；并发取消/发布只允许一个一致结局；旧 token、过期租约、旧 seq 均拒绝；偷偷改变审核正文拒绝。读取报告 owner-scoped，越权返回无记录。Fake 提供同步契约，不是 PG 证据。

验证：最初 7 项报告反例加入后全量334 passed，43.70s；后补取消竞争与三项 fencing 后，报告测试11 passed，2.02s。Ruff 通过。测试库精确清理，用户数据库未迁移。

T015 的存储主要操作已具备；Runner/HTTP/SSE 尚未全部接入，T014/T015 保持未勾选。
