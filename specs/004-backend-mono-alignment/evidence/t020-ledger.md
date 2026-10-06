# T020 PG 调用账本与真实缓存闭环（部分）

## 已实现

- 迁移 `0003_tool_call_attempts` 为语义调用保留一个 ToolCallRecord，并为每次实际尝试追加独立 attempt、领取 token、token 预留/已知用量、uncertain_replay。缓存 ContentRef 的大小与媒体类型进入 PG。
- 预算 baseline 只在首次调用建立，来源为已提交 Checkpoint；后续快照不重新播种已用计数，避免重复计费。settled 与 pending 分开，未知消耗按预留上界保守记账，不当作实测 tokens；已知失败仍保留实际用量。
- reserve 与预算读取在同一 Session→Run 锁和租约事务，跨连接并发受 PG 约束。80 个 search 预留仅 60 个成功；相同语义并发只产生 1 次预留。
- 新租约领取时，把所有旧租约未完成尝试改 uncertain；不是仅处理首先重试的 query。恢复不会清预算。只读 llm/search/fetch 才能显式 uncertain 重放，每次重放新增 attempt 并计数；analysis 的未知尝试不能这样重放。
- `ToolCallService` 提交 reserve 后才发外部 I/O，不持 PG 长事务；成功内容先写真实 MinIO、验证 hash，再提交 PG succeeded。新服务实例及新租约可读缓存，不重新调用工具。缓存丢失/损坏明确失败，不静默重发收费请求。
- 取消阻止新预留，已经完成的 I/O 可以安全结算。取消异常持久化有强引用与观察，旧租约不能更新结果。SQL故障/提交期间租约过期回滚全部预留写入。
- 供应商返回的 tokens 超出预留时保留实际用量、停止后续调用，不伪造较小数值。Checkpoint 的计数必须与 durable settled ledger 相同；不能重置或把 baseline 再加一遍；发布时不得仍有未结算调用。

## 测试

使用本轮独立真实 PG `dr4a-verify-pg-20261006-1041` 的进程级 DSN；所有 fixture 的 DB/bucket 是唯一生成且在 teardown 精确清理，原损坏 PG 与原卷未修改。

- 迁移套件 11 项通过，包含重复迁移/故障回滚/并发迁移；编号追加，不修改已应用的 0002。
- 首批工具账本与已有 Run/Report 事务回归 **45 passed in 9.41s**。
- 补全 MinIO 服务、恢复、计量等后目标集 **15 passed in 4.04s**。
- 新租约全量 reclassify 与 TaskRunner 联合目标集 **22 passed in 9.53s**；这里是 16 项工具测试 + 6 项 Runner 测试。
- 最终全量 **468 passed in 57.08s**，包含失租/已知失败/reclassify及缓存跨新租约反例。

早期服务测试发现 `RunConfig.timeouts_s` 被误写为 timeouts，实际工具未调用；修正字段并重新验证，失败批次不算通过。取消测试等待有界，finally 会观察/取消自身测试 task；不会因为前置失败无限等事件。

## 真实供应商探针

2026-10-06，使用 `.env` 已授权凭据，公开的短基础设施请求；真实 DeepSeek 单次请求→真实 MinIO→PG succeeded→另一服务实例取相同语义缓存。不是研究报告 E2E；没有把 SDK mock 当真实模型。

```json
{"mode":"real","database":"dr4a_test_550c331988e7445cb04fb02f0b06d9f7","bucket":"dr4a-test-65555c0371724cf3a6d72b670f6ac263","run_id":"ce0dc0fd-aada-450d-84a7-eac4b120afaf","call_id":"tool_131b881771348e7f7837d60419c46789c167243bd6d325dc27be8f3e7e90e4a9","provider_calls":1,"attempts":1,"status":"succeeded","response_id":"4e1bd376-ed0d-4e9f-bfe6-2536821eb817","model":"deepseek-flash","input_tokens":41,"output_tokens":29,"charged_tokens":70,"result_hash":"6f770e5d9c531ccf764e68ece8f983bdf19ff32991dec947e8ec56ddf159b3a5","result_object_key":"tool-results/ce0dc0fd-aada-450d-84a7-eac4b120afaf/6f770e5d9c531ccf764e68ece8f983bdf19ff32991dec947e8ec56ddf159b3a5","cached_equal":true}
```

探针 DB 与 bucket 均已精确删除。没有保存费用，不将供应商 alias 当不可变 revision。

## 尚未完成

T020 仍不勾选：预算/cache callback 尚未绑定正式 phase/Orchestrator；“内容对象已保存而 PG succeeded 尚未提交”的恢复定位仍待补。该窗口可留下 orphan object，当前必须明确 uncertain 才能只读重发；不能声称物理 exactly-once。供应商实际 tokens 也不能靠本地预留函数强制，异常超界的处理是记录真实用量并停止，而不是隐瞒费用。

真实独立进程 SIGKILL 的恢复证据属于 T022，仍待执行。以上事务/缓存验证不证明五阶段业务或三种报告验收。
