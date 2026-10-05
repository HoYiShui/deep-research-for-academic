# T007 身份持久化与 SessionView 读边界

2026-10-05，第二批；**T007仍未完成**。设计来源：API §1.1/§2.4、FLOW §1、MODEL Session/Run/Checkpoint。

## 实现

- `application/identity.py` 使用 UnitOfWork 和 UserRepository 创建固定开发用户；固定UUID集中在 `application/records.py`，预留内部邮箱为 `development@dr4a.invalid`，无密码、不能登录。`DevelopmentUser` 校验固定身份，不接受随意owner。
- Repository新增 `ensure_development`。PG使用 `INSERT ... ON CONFLICT DO NOTHING` 后读取原记录，保留首次创建时间；UUID/邮箱冲突或原身份不是开发用户，返回service_not_ready，不改写用户或借用另一个owner。fake使用相同政策，不代替PG证明。
- `interface/main.py` 在发布容器前允许异步 `prepare()`，失败仍关闭已创建容器。测试runtime通过该钩子实际调用PG身份初始化；没有偷偷给旧默认容器自动升级用户数据库。
- `application/research_queries.py` 从 owner-scoped Repository生成 SessionView，在短事务锁定Session后读取对应Run及其checkpoint_seq；不按phase倒序挑快照，不启动或恢复任务。检查Session/Run/Checkpoint、配置/来源/冻结hash一致性；异常状态不当成功交付。
- 实际 `GET /research/{session_id}` 已调用新投影路径，session_id校验UUID，owner从认证依赖传递。未装配ResearchQueries返回503 service_not_ready，不退回旧“缺记录也返回clarify”路径。默认组合根暂未接入mono存储，故该GET目前会503，需后续接入完成后才可正常读取业务状态。
- 开发身份固定；正式token解出的非UUID身份返回401，不把格式错误传入业务。JWT完整正式校验仍属T053，不声称已完成。
- 旧auth guard测试改用合法UUID与受控SessionView桩，仅证明身份传递；不保留非UUID身份/clarify status旧业务断言，不将受控桩称真实会话。

## 验证

在backend目录：

```bash
uv run pytest -q tests/integration/test_development_identity_pg.py tests/unit/test_development_identity.py
uv run pytest -q tests/integration/test_mono_session_view_http.py tests/integration/test_auth_guard.py tests/integration/test_mono_http_errors.py
uv run pytest -q
```

- 3项fake身份反例+4项真实PG身份测试通过：4个并发启动只得到一个相同User；固定UUID/邮箱占用不覆盖；共享事务注入异常后无半创建。
- 真实PG+ASGI HTTP通过实际GET路由：启动prepare写入开发User；自己confirm可读完整Brief；其他owner与不存在资源均404；非法UUID422；事务冻结后ready/plan/seq1投影正确；只读后Run仍ready且attempt_count=0；故意破坏Session/Run状态配对后503。
- HTTP使用 httpx.ASGITransport 和显式受控runtime/lifespan，PG是活隔离数据库；Session/冻结fixture通过Repository准备，**不是**通过POST创建/确认，不是独立uvicorn真实模型业务E2E。T013仍须完成外部HTTP和真实模型会话证据。
- 全量 **242 passed in 10.39s**；修改路径ruff All checks passed；git diff --check通过。
- 身份模块尚未存在时新测试收集失败；改GET后旧auth测试非UUID路径422实际失败，随后按目标UUID契约替换旧断言。

## 未完成与数据保护

默认组合根还在旧写服务上；需将新Repository/身份初始化正式接入默认启动，并替换ResearchService/SessionService与请求DTO，再验证所有写接口owner归属。T007不勾选；读取端已经不允许退回虚构状态。T008–T012的会话接入将完成这些边界，不能长期保留新读/旧写两套路径。

本批无收费模型、无真实用户库迁移/写入；PG测试只创建/删除本轮fixture明确命名的测试数据库。不改backend/.env、用户成果或未跟踪docs/implementation，不推送。
