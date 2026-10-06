# US2 活 HTTP Run 验证（部分）

2026-10-06，基线 `d889641`。真实PG/MinIO、真实TCP、独立HTTP服务与CLI探针进程；研究业务输出为显式受控fixture，不是A6真实报告。

## 可运行探针

在backend中先使用已有 `scripts.verify_clarify_http` 创建会话、提交回答并显式确认审阅过的Brief。随后：

```bash
uv run python -m scripts.verify_run_http --session SESSION_UUID --model-mode real
uv run python -m scripts.verify_run_http --session SESSION_UUID --action cancel --model-mode real
uv run python -m scripts.verify_run_http --session SESSION_UUID --action resume --model-mode real
```

`--url`默认http://127.0.0.1:8000；`--timeout`为整个观察过程的deadline。`--model-mode`只是操作者声明，不能证明供应商/业务真实。受控测试必须明确填controlled。默认只读，不确认、不恢复、不创建Run；控制请求仅在显式action时发送带幂等key的POST。resume从新读取的failed/resume_allowed视图取checkpoint_seq，不自行挑阶段。探针不直接操作SQL。

输出一个JSON，包含HTTP trace、合法事件、终态view及已完成报告。SSE必须是正确媒体类型、完整Frame Schema、相同Session/Run且seq不倒退；done必须与新GET的Run状态/seq一致，completed还核对报告verdict。断流/超时/契约不符以失败退出，不当成done，也不自动付费重跑。当前开发匿名环境可用；正式Bearer/cookie探针凭据尚待T053一起接入。

## 活 TCP 测试证据

`tests/integration/test_mono_run_tcp.py` 在唯一临时PG和MinIO bucket上启动独立uvicorn，走201创建→200澄清→202明确确认→真正http.stream逐帧读取→GET报告。

- 最终SSE `completed`、Checkpoint seq=20、review_verdict=needs_more_work。
- SQL核对reports=1、tool_call_attempts=1、Run attempt_count=1。
- 终止该HTTP进程并启动全新进程；GET状态/报告相同，迟到SSE仍有done。
- 独立 `python -m scripts.verify_run_http` 子进程通过，stdout单JSON；再次SQL核对attempt/调用数仍1。
- 测试only server明确校验DR4A_TEST_HTTP_MODE和测试库/bucket名称；生产入口没有受控fallback。

首次活TCP+已有Clarify探针 **4 passed in 16.59s**；加入真实探针CLI子进程后 **4 passed in 16.25s**。探针10项单测通过，覆盖错Run/回退seq/假completed/HTTP-SSE不一致/媒体类型/无done/显式action与不可恢复拒绝。

最终联合回归（run_tcp、verify_clarify_http、runtime_runner、process_recovery、verify_run_http单测）：**21 passed in 20.05s**，包括原有SIGKILL窗口未回归；ruff/format与git diff --check通过。本批不重复宣称全量测试，上一基线全量773通过。

## 未完成

本批进程重启是正常终止，不声称SIGKILL。已有tool cache与Driver独立SIGKILL证据继续保留，但T022要求的“HTTP确认提交未wake”和“返工阶段中断”仍须补到同一链路，HTTP cancel/resume也需真实组合反例。T022保持未完成；此处受控报告不能替代T028/T039的真实取证/三任务报告。
