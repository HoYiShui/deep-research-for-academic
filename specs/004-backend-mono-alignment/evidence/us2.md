# US2 活 HTTP Run 与恢复验证

2026-10-06，基线 `d889641`。真实PG/MinIO、真实TCP、独立HTTP服务与CLI探针进程；研究业务输出为显式受控fixture，不是A6真实报告。

## 可运行探针

在backend中先使用已有 `scripts.verify_clarify_http` 创建会话、提交回答并显式确认审阅过的Brief。随后：

```bash
uv run python -m scripts.verify_run_http --session SESSION_UUID --model-mode real
uv run python -m scripts.verify_run_http --session SESSION_UUID --action cancel --model-mode real
uv run python -m scripts.verify_run_http --session SESSION_UUID --action resume --model-mode real
```

`--url`默认http://127.0.0.1:8000；`--timeout`为整个观察过程的deadline。`--model-mode`只是操作者声明，不能证明供应商/业务真实。受控测试必须明确填controlled。仅传已有Session时只读；控制请求仅在显式action时发送带幂等key的POST。resume从新读取的failed/resume_allowed视图取checkpoint_seq，不自行挑阶段。探针不直接操作SQL。

同一入口支持`--query`新建、`--session UUID --answers-file FILE`回答，以及审阅后`--session UUID --approve-file FILE`明确确认。文件格式复用Clarify探针；未提供确认时输出ask/confirm及approval_required，不自动接受假设。创建/澄清不能与Run action混用。

输出一个JSON，包含HTTP trace、合法事件、终态view及已完成报告。SSE必须是正确媒体类型、完整Frame Schema、相同Session/Run且seq不倒退；done必须与新GET的Run状态/seq一致，completed还核对报告verdict。断流/超时/契约不符以失败退出，不当成done，也不自动付费重跑。当前开发匿名环境可用；正式Bearer/cookie探针凭据尚待T053一起接入。

## 活 TCP 测试证据

`tests/integration/test_mono_run_tcp.py` 在唯一临时PG和MinIO bucket上启动独立uvicorn，走201创建→200澄清→202明确确认→真正http.stream逐帧读取→GET报告。

- 最终SSE `completed`、Checkpoint seq=20、review_verdict=needs_more_work。
- SQL核对reports=1、tool_call_attempts=1、Run attempt_count=1。
- 终止该HTTP进程并启动全新进程；GET状态/报告相同，迟到SSE仍有done。
- 独立 `python -m scripts.verify_run_http` 子进程通过，stdout单JSON；再次SQL核对attempt/调用数仍1。
- 测试only server明确校验DR4A_TEST_HTTP_MODE和测试库/bucket名称；生产入口没有受控fallback。

首次活TCP+已有Clarify探针 **4 passed in 16.59s**；加入真实探针CLI子进程后 **4 passed in 16.25s**。探针10项单测通过，覆盖错Run/回退seq/假completed/HTTP-SSE不一致/媒体类型/无done/显式action与不可恢复拒绝。

上一批联合回归（run_tcp、verify_clarify_http、runtime_runner、process_recovery、verify_run_http单测）：**21 passed in 20.05s**，包含原有SIGKILL窗口回归；ruff/format与git diff --check通过。上一批未重跑全量，其前一基线全量773通过。

## T022完成复核

基线`97e6194`。新增真实HTTP/独立进程测试：

- 确认事务已提交、wake尚未发生：test-only wake回调SIGSTOP整个子进程，父进程通过ps确认T状态、PG ready/seq1/attempt0；发送SIGKILL并验证退出码-9。新进程扫描ready后正常完成seq20/attempt1；同幂等key重发确认返回原Run，Run/report各1。
- 返工中断：首次review请求write返工，已提交rework_count=1、phase=write后SIGKILL。只推进本轮唯一Run的测试租约过期，新进程扫描为failed/interrupted；seq保持不变，attempt1，报告409，迟到SSE只有failed不触发执行。探针HTTP显式resume后同Run completed/seq24/attempt2，tool_call_attempts仍1、reports1，缓存plan不再次付费调用。
- 运行中取消：真实plan到research/seq3，worker等待取消检查；探针POST cancel、SSE done.cancelled、report409；重复取消仍cancelled，reports0、模型attempt1、Run attempt1、不可resume。
- 独立CLI探针三次调用：query返回ask，回答返回confirm，前两次SQL Run数0；只有提供审阅的完整确认文件才到completed/seq20，Run/report各1。
- 原有成功缓存独立SIGKILL写入前/后与单元→阶段间窗口继续回归，详见同文件`test_sigkill_staged_result_recovers_without_reset_or_implicit_replay`和`test_sigkill_between_unit_and_phase_commits_resumes_full_driver_without_paid_plan_replay`。

最初新增两个HTTP SIGKILL与原窗口 **5 passed in 17.18s**；补ps明确窗口及运行中取消后，与TCP/Clarify探针联合 **10 passed in 38.46s**；CLI创建/确认与探针单测 **12 passed in 14.71s**。全部依赖用唯一隔离PG/bucket，未更新用户历史库/卷；SIGKILL只对本测试持有的child process handle发送。

最终全量 `uv run pytest -q --tb=short`：**788 passed in 126.82s**。ruff/format及git diff --check通过。T022按其受控调度验收勾选，其他未完成任务不随之勾选。

## 范围限制

早期批次只有正常重启；上述完成复核才补齐真正SIGKILL。T022完成指受控生命周期验证，不代表默认业务worker已可用，不代表JWT/正式readiness完成，也不能替代T028/T039真实取证/三任务报告。生产Server不导入这些test-only窗口或受控worker。
