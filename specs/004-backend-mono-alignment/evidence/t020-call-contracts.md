# T020 调用身份与模型计量（部分）

## 实现

- `ToolCallIdentity` 纳入 Run、工具/供应商/版本、完整参数、输入 hash、冻结来源范围及知识版本；不接受 attempt/timestamp。来源选择顺序不改变身份，真实正文空格或版本改变会改变身份。不合规 JSON、NaN、越界知识版本在 I/O 前拒绝。
- `ToolCallRecord` 对齐当前表字段，成功要求对象 key/hash，结果键/hash必须成对，时间不可倒退。尚未接 PG reserve/settle 事务，不能以此宣称账本可用。
- `complete_metered()` 每次只发一个模型请求，返回请求局部的 response ID、供应商 model 字段、正文、停止原因和供应商 input/output token 用量。无共享 last_usage；不使用估算代替返回的 usage，缺失/错误 usage 明确失败。
- 截断响应仍保留用量；之后由预算协调器先记消耗，再拒绝或修复正文。原 `complete()` 暂保留给未迁移调用方，不代表其已计入持久预算。

## 证据与边界

2026-10-06，恢复磁盘后单元与契约全量 **270 passed in 16.24s**。调用身份、模型计量及原文本接口的目标集在断连前 **19 passed in 0.69s**；受控供应商 mock 测试覆盖截断、缺 usage、错误类型、并发结果隔离、取消和零隐式重试。这不是缓存恢复 E2E。

此前进行了一次真实公开基础设施探针（不是研究流程）：

```json
{"mode":"real","attempts":1,"model":"deepseek-flash","response_id":"523a715e-cc9e-4e44-a2bf-2a2152a26d94","stop_reason":"end_turn","input_tokens":44,"output_tokens":29,"total_tokens":73,"elapsed_s":0.7}
```

供应商 model 字段是 alias，不是不可变 revision；未记录费用，不将 token 用量转换为实测金额。探针无 Session/Run/Report、无预算事务。

磁盘断连后的全量测试第一次 SIGBUS；恢复后另一次因 PostgreSQL 容器退出（139），Docker 内部文件系统只读，产生依赖错误，已终止，**不能记为通过**。尝试启动原 postgres 容器后状态仍 exited，不重建数据库、不删卷。等待 Docker 恢复后重跑真实 PG 与完整回归。

仍待：PG预算并发预留、缓存成功关联、uncertain只读重放记录、恢复保留预算、协调器/Runner组合。T020不勾选。
