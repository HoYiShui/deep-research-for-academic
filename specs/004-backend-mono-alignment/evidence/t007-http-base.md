# T007 HTTP 底座：首批验证

日期：2026-10-05。T007 **未完成**；本记录仅证明错误/装配基础，不证明研究业务已对齐。

## 实现与设计来源

- API §1 的公共失败形状在 `backend/interface/http_errors.py` 统一实现：AppError 的闭集状态码、AdapterError→503、未知异常→脱敏500、JSON语法错误400、请求校验422；错误响应与成功响应均含服务端生成的 X-Request-ID。
- `RequestIdMiddleware` 使用原生 ASGI，不缓存/拼接 SSE body。客户端提供的请求 ID 不被信任。Adapter 异常、未知异常、校验输入与 validator ctx 不返回客户端；AppError 的明确公共 message/details 由用例负责安全内容。
- `request_in_progress`/`rate_limited` 的 Retry-After 采用安全正整数 `details.retry_after_s`，没有该字段时默认5秒。反例曾得到错误5秒而预期91秒，修正后通过。
- `interface/dto/base.py` 使现有请求 DTO 拒绝未知字段，尚未替换其完整字段/枚举为 mono DTO（后续T012/T047/T053）。响应 Schema 暂保留旧业务接口。
- `interface/main.py:create_app` 在 lifespan 先校验 Settings，随后装配独立容器；支持显式 container_factory 注入受控模型/服务。HTTP Router 根据 Request 获取当前应用容器，不再使用 CLI 全局单例。设置在启动后不随环境变更而漂移。
- `Container.aclose()` 关闭去重后的可关闭 Adapter，即使其中一个失败仍尝试剩余关闭；模型 SDK 新增 aclose，旧 PG Store 关闭有5秒上界，超时只 terminate 自己持有的 pool。后台任务强引用/停止归T016，不据此声称已完成整个运行生命周期。
- development identity 改为 API §1.1 固定 UUID `00000000-0000-4000-8000-000000000001`，但当前旧HTTP服务未将其写入新 users，亦未传 owner 到业务用例。这是尚未完成的接入，不是归属验证通过。

## 命令与实测

在 `backend/`：

```bash
uv run pytest -q tests/integration/test_mono_http_errors.py
uv run ruff check interface application/bootstrap.py infrastructure/storage/postgres.py infrastructure/llm/deepseek.py tests/integration/test_mono_http_errors.py
uv run pytest -q
```

- 初始新测试因尚不存在 http_errors 模块失败，随后补实现；新增 Retry-After 反例实际失败后修复。
- 新 HTTP 测试20项，覆盖实际应用未知字段拒绝、受控错误端点、两个应用隔离、配置固定、生产匿名启动失败、去重关闭/关闭失败继续、PG pool超时关闭。
- 修改路径 ruff：All checks passed。
- 最后全量：**232 passed in 6.28s**。包含既有真实隔离PG测试；本批HTTP使用 TestClient/受控失败，不是活服务器真实模型E2E。
- `git diff --check` 通过。

## 未完成与保护边界

T007还需新Repository组合根、开发User落库、服务端owner传递与真实HTTP跨owner404；完整 mono 请求/响应接入需与会话用例一起完成，不在旧服务上伪造归属或确认闭环。现有旧Clarify/KB业务仍不可视为目标设计。

未运行用户数据库迁移、未清理已有数据、未调用收费模型；真实存储回归只使用测试 fixture 新建的隔离数据库。未修改或提交 `docs/implementation/`、backend/.env，未推送。
