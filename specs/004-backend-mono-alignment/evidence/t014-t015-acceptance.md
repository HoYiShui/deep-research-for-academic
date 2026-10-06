# T014/T015 完成复核

2026-10-06，基线 `5fa8f57`。此记录取代早期批次中的“尚未完成”状态，不更改历史测试结果。

在 backend 执行：

```bash
uv run pytest -q --tb=short tests/integration/test_mono_run_lifecycle.py tests/integration/test_mono_sse.py tests/integration/test_mono_report_publication.py
```

结果：**53 passed in 11.18s**。PG测试在恢复后的服务上创建唯一临时数据库，未迁移或修改用户实际 deepresearch 数据库。

## 对应验收

- 领取竞争与容量：同Run竞争、全局/owner限额、CLI owner+run范围；Session写入故障回滚领取。
- 租约与Checkpoint：PG时钟续租、旧worker/token、过期及事务内过期拒写；expected_seq、完整冻结输入/预算校验；返工后按Run当前seq而非phase大小读取。
- 取消与恢复：取消优先、并发resume、同Run/预算/config保留、两个扫描器竞争、排队超时；故障不留下半更新终态。
- 报告发布：Report/done Checkpoint/Run/Session四事实同事务；四处SQL故障回滚、真实取消/发布竞争、旧租约拒绝、审核文本不可偷改。
- SSE反例：独立订阅队列、慢订阅者独立关闭、订阅/PG bootstrap竞态、迟到completed/failed、同seq状态不倒退、断开不取消任务、PG不可用只诊断不制造done。订阅和重连不调用执行器。

Repository只进行短事务事实读写，不执行模型/网络工具；工具I/O与事务分离还由已有tool cache及Driver测试覆盖。此处不声称默认HTTP已组合完整执行器，也不声称SSE活TCP、JWT截止、CLI run或真实研究报告完成；这些继续由T016–T022、T028/T039验收。
