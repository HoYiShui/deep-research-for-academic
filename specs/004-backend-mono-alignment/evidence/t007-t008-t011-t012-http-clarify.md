# HTTP Clarify、显式确认与原子冻结接入

2026-10-06。设计来源：MODEL §2–3、FLOW §2、API §1–2、OPS §2–3。

## 实现与范围

- T007 默认 HTTP 改为 HttpRuntime：真实 asyncpg pool、受保护迁移、新 PG Repository、固定开发 User、严格 SessionService/ResearchService/ResearchQueries。显式测试组合根可注入受控模型；正式默认没有 FakeLLM、内存 Session 或旧 StateStore 回退。生命周期关闭 SDK/自己的 pool，不关闭调用者 pool。生产鉴权强制开启；JWT/登录 T053 接入前失败关闭。
- T008/T012 实际路由接入201 Ask/Confirm、200回答/退回、202确认、GET SessionView。传 owner/key/version；严格输入拒绝未知字段、null伪可选字段、非法任务类型。ask/confirm无SSE URL。读取按Session+Run+checkpoint_seq投影，不按phase倒序。
- T011 公开来源主干：先短事务预留幂等租约，模型在事务外，再CAS提交候选和响应缓存。长请求续租，失败/取消释放键。确认同一事务保存冻结Brief、唯一Run、完整seq=1 Checkpoint、确认消息、Session与202缓存；不同键并发确认返回同一Run。start_frozen复用校验/事务并记录CLI身份，不调用模型，不留半创建Session。
- HTTP已不使用旧内存SSE队列/取消标记。冻结前events409；后续事件、取消和发布报告读取在专属任务接入前明确503，不虚构成功。
- KB owner授权/版本锁定/隐私桥接待T041/T050；当前KB ID均404且模型调用零，不接受旧内存KB。因此 **T011保持未勾选**，公开来源不能替代私有来源验收。

## 验证

新HTTP测试先因缺HttpRuntime失败，再实施接入。使用实际FastAPI/ASGI HTTP、真实隔离PG和受控模型；没有独立uvicorn/收费模型，不能替代T013。

`tests/integration/test_mono_clarify_http.py` 共22项：

- 初始不足/充分201、后续200、确认前零Run；重放不调用模型/增加轮次；同键不同输入409。
- 两条消息并发只有一个CAS成功；不同键并发确认只有一个Run/Checkpoint/确认消息。
- 模型失败零Session/同键可重试；缺键/控制字段/非法确认422；跨owner六种读写均404且零模型调用。
- 3轮上限后停模型、显式patch、退回200ask，无自动Run。
- Run/Checkpoint插入时真实SQL除零：冻结与缓存全部回滚，同键重试202。
- CLI冻结故障连初始Session/Brief/messages一并回滚；正常重放同一202、confirmed_by正确、零模型调用。
- 实际PG续租；续租失败取消阻塞模型并释放键；取消HTTP工作任务无半Session/残留键。
- 来源反例发现UUID进入Python dict哈希导致TypeError/500，修正为JSON模式规范化；未知KB现在404且零模型调用。

在backend执行：

```bash
uv run pytest -q
uv run ruff check application/research_service.py application/research_inputs.py application/bootstrap.py interface tests/integration/test_mono_clarify_http.py tests/integration/test_mono_http_errors.py
```

最后全量：**290 passed in 16.31s**；修改路径lint通过，git diff --check通过。所有PG写入只在fixture唯一`dr4a_test_<uuid>`数据库，准确关闭/删除。没有迁移用户实际库、修改.env、打印凭据、推送或包含用户docs/implementation。

## 后续

T007/T008/T012接入已完成。T011尚缺KB授权/隐私。T013仍需独立活HTTP工具、真实模型多轮、重启恢复与只读SQL证据；M1未完成。LegacyResearchService/LegacySessionService仅兼容旧CLI/测试，T021必须移除正式入口。TaskRunner、事件、取消、报告、Web尚未完成，不以本批绿灯宣称全系统可用。
