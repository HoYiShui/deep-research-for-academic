# T014/T015 首批：真实 Run 领取与单调 Checkpoint

2026-10-06。基线 `9b4b079` 加本批变更。设计来源：MODEL §3、FLOW §3.1/§4、API Repository 契约、OPS §3–4。

## 实现

- ResearchRepository 增加 typed ClaimedRun、claim_run/renew_lease/load_latest_checkpoint/commit_checkpoint；真实实现拆分 `infrastructure/storage/run_leases.py`，由正式 PG Repository 接入，不新增第二套任务事实源。
- 领取采用 PG 事务 advisory lock保护全库容量计数，Session行锁/skip locked、条件UPDATE领取ready。全局默认2、每owner1；CLI可指定owner+run，不能只给run绕过owner。用PG时间写90s租约、递增token/attempt；Session.running与Run.running同事务。过期running不被直接重新领取/付费重跑。
- 续租检owner/session/run/worker/token/未过期，PG时间延长；错误worker/token和过期拒绝。后续扫描器和Runner尚未接入，不能把它当完成执行链路。
- Checkpoint事务先锁父Session再锁Run，检当前持租身份与expected_seq、完整Schema/hash、冻结Brief/版本/来源/config身份，预算/elapsed/rework不倒退。保存seq+1并条件推进Run.phase/checkpoint_seq、Session.revision，同事务；再次检租约未到期。done/FinalReport不允许普通Checkpoint提交，必须后续报告发布事务。
- 最新读取是Run.checkpoint_seq的直接JOIN，不按phase/max阶段；旧快照不更新。FakeResearchDatabase同步实现相同接口/边界，仅作为受控double；它不代替PG证据。

## 反例

先写领取测试，6项因缺接口失败；之后先写Checkpoint测试，7项因缺接口失败，再实施。测试均使用fixture生成的唯一隔离PG，未修改用户实际库。

`tests/integration/test_mono_run_lifecycle.py` 当前14项：同Run两个worker竞争；6个ready来自3个owner，6次并发领取只能2个不同owner；CLI owner/run范围；PG续租及旧worker/token；过期不复活；初始owner读取；Session更新故障回滚Run租约/token/attempt；research→analyze→research回流seq1..4及旧快照保持；旧token/过期/旧seq/预算回退/改冻结Brief拒绝；Checkpoint插入SQL除零不推进Run或Session。

所有并发gather使用return_exceptions等待全部协程收尾，防初次缺接口时早抛异常造成连接未归还。最初红灯的一个临时库 `dr4a_test_755631de8e5341df9403111eea0d2414` 留下；独立只读检查该精确库名exists=1、active_connections=0后准确删除，未force终止其他连接/删除其他库。后续测试正常收尾。不能把测试清理错误隐藏为全部首次通过。

在backend执行：

```bash
uv run pytest -q tests/integration/test_mono_run_lifecycle.py tests/contract/test_mono_ports.py
uv run pytest -q
uv run ruff check application/ports.py application/records.py infrastructure/fake_research.py infrastructure/storage/run_leases.py infrastructure/storage/research_postgres.py tests/integration/test_mono_run_lifecycle.py tests/contract/test_mono_ports.py
```

定向 **23 passed in 2.84s**，其中14项真实PG、9项原有/新增Fake契约；全量 **314 passed in 39.66s**。lint与git diff --check通过。早期Pydantic实例model_fields警告已改成类级访问；最终定向无该警告。

## 未完成项

**T014/T015均保持未勾选**：尚缺持久取消/resume、过期扫描/队列超时/ready容量、报告与终态原子发布及竞争反例；SSE迟到/多订阅/失败done仍待T018。T016尚未启用领取扫描，默认HTTP仍确认后ready排队，而events/cancel/report暂明确未就绪。T020工具预算的事务预留/结果缓存尚未实现，当前仅证明Checkpoint不能回退已记录消耗；不把它写成已解决工具物理调用恰好一次。控制阶段fixture只验证存储，不证明真实研究质量、报告或Web通过。

下批从同一持租/父子锁序扩展取消、失败、扫描、resume和发布，再接TaskRunner；goal继续，未推送、未包含用户docs/implementation、未改.env。
