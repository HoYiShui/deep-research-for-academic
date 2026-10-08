# T037：私有附件 HTTP 下载

日期：2026-10-08；基线 `9c8f9a2`。设计：API §2.5、MODEL §4.3、OPS §6；实现落点为 `application/research_artifacts.py`、`infrastructure/storage/artifacts.py` 与既有 Research router/runtime。

## 边界

使用 owner-scoped ResearchQueries 读取同一事务中的当前 Run/Checkpoint，不先访问 MinIO。仅当前 Checkpoint 注册的 completed Artifact 与白名单 basename 可读；发布的 Report 与 done Checkpoint 已有原子一致性约束。映射沿用报告 serializer 的 object_keys 最后一段规则，不新增 State 字段、不接受客户端 object_key。

明确 App 生成的不可变存储键：`analysis/{run_id}/{artifact_id}/{sha256}/{basename}`。专用 ArtifactStorePort/MinioArtifactStore 不修改已有正文/工具缓存键。写入校验实际 hash；同键不同字节禁止写入；私有读限制 10MiB，并复验实际字节 hash。注册的键仍须匹配当前 Run/Artifact，不能借合法记录跨命名空间读取。

HTTP 返回原始字节、白名单媒体类型、attachment Content-Disposition、nosniff、private/no-store。读取并验证完成后才发送 200；对象缺失、损坏、超限或无效持久化键统一脱敏 503 content_unavailable。不返回对象键、公开 MinIO 地址或 SDK 异常。不存在/未登记/不合法文件为 404；跨 owner 由共享查询返回 session_not_found。下载不改变会话状态，不执行业务 Agent。

Runtime 装配并关闭私有存储；关闭失败也会进入 PG 收尾。不创建用户 bucket，不迁移或清理用户数据库，不启动 Compose PostgreSQL，不修改 .env 或 docs/implementation。

## 验证

`uv run --no-sync pytest -q tests/integration/test_mono_artifacts.py --tb=short`：**14 passed in 4.30s**。HTTP 使用 ASGI 客户端，PG/MinIO 为真实独立资源；无模型调用，也不称真实报告质量验收。

覆盖真实 CSV 下载及响应头；跨 owner 在存储 I/O 前拒绝；未知 Artifact/文件、HTML、反斜杠/编码斜杠穿越、CRLF 文件名 404；MinIO 对象缺失/篡改/超限 503 且不泄漏；已注册跨 Run/Artifact 键不读存储；skipped Artifact 不下载；并发同字节写入、错误 hash 与非法键拒绝。只清理 fixture 创建的 dr4a_test_* 数据库、dr4a-test-* bucket。

邻接 HTTP 回归最初 **32 passed in 5.55s**；新增命名空间与写入检查后目标集增至上述 14 项。7 个改动 Python 文件 Ruff 与 format 检查通过。首轮测试的 Runtime 缺 aclose 导致收尾错误、后续测试误用 ResearchRun.owner_id，均修正为合法 fixture 生命周期及 ClaimedRun.owner_id，最终无错误。

最终全量：`uv run --no-sync pytest -q --tb=short`，**996 passed in 212.62s**，包含本批 14 项真实存储 HTTP 测试。

本批验证的是附件传输与存储边界，不声称 T031 分析模板、实际 Agent 产图、T039 报告质量或 T057 生产部署已完成。
