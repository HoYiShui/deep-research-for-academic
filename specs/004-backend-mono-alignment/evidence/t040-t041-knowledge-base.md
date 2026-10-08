# T040 / T041：知识库事实与原子版本发布底座（部分）

日期：2026-10-08；基线 `3e44ca8`。依据 MODEL §5–6、FLOW §5–7、API §4、OPS §5。本批不接管 Agent；不宣称完整知识库入库、向量检索或真实 PDF 成果闭环。

## 已实现

`application/knowledge_models.py` 包含 KB/Document/Version/Job/Attempt/Progress/Chunk、RetrievalResult/SourceMetadata/Trace/VectorHit/RankedHit。复用严格 Record/Location/Failure；拒绝额外字段、bool 冒充计数、非有限得分、缺定位、重复批次、无租约 processing、缺失尝试历史与未发布 active 版本。private 默认；历史 completed Job 不要求版本永远 active。

新增追加迁移 `0005_mono_knowledge.sql`（0003/0004 已被工具调用使用，不重编号）。五张表、owner 内活名称唯一、复合父子 FK、内容身份唯一、Document 单 active 版本及单活动 Job、Chunk ordinal 唯一；延迟约束验证 Document.active_version_id 与版本 active 状态同一事务一致。没有 cascade 删除成果或改写历史表。原迁移测试的版本总数由 4 更新到 5，其他迁移原子性/独立进程竞争测试保留。

`KnowledgeRepository` 与 Research 共用同一个 PostgresResearchStore/UoW，不建立第二个可写事实源。owner 查询、上传后 submit、锁 KB/Document/Job、领取带递增 fencing token 的尝试、失败记录、原子 activate。内容重复复用原 Version/Job；显式 Document 冲突返回 content_identity_conflict；活动 Job 冲突 document_busy 且回滚候选版本。

activate 校验资源状态、当前租约/取消、profile、连续非空 chunk manifest、对象 scope/hash 与不可变 Chunk 身份；同事务插 Chunk、旧版本 retired、新版本 active、更新 Document pointer/revision、完成 Job/尝试。结束写 Job 时再次以 SQL 时钟检查 token/expiry/status/cancel，不能仅在长事务入口检查租约。任一失败回滚所有发布事实，旧 active 保留。

新的 KB ContentStore 键为 `knowledge-content/{owner}/{kb}/{version}/{hash}`；只允许指定 KB/版本前缀清理，不允许 owner 根前缀。保留已有 documents/research-content 内容 Adapter 命名空间兼容性，不把它当新 KB 的 owner 命名空间。MinIO 字节仍按实际 hash 验证；服务不接受客户端存储键。

## 验证与发现

`uv run --no-sync pytest -q tests/unit/test_knowledge_models.py tests/integration/test_mono_kb_lifecycle.py tests/integration/test_mono_migrations.py tests/integration/test_mono_document_content.py --tb=short`：**62 passed in 8.33s**。真实独立 PG 和 MinIO；KB/Version/Chunk 是显式 metadata fixture，无真实 Parser/Embedding/Milvus 调用，不能把原子激活的受控成功信号称完整入库成功。

实际验证：跨 owner 不可见；staging 没有可见 Chunk；两个并发重复请求收敛同 Version/Job、没有多余 Document；单活动 Job/复合 FK/延迟 pointer 约束；替换后旧 Job completed/版本 retired 合法；注入发布 SQL 故障回滚；失败替换仍可见旧版本；错 token、已过期、取消、KB/Document 删除屏障拒绝；真实 pg_sleep 延迟 Chunk 写入令租约在事务中途到期，最终发布拒绝并回滚；源 key 的 owner/KB/version/hash 错位拒绝；真实新命名空间字节回读，清理本版本/本 KB 不影响其他 KB/owner。

初轮迁移回归发现两个旧总数断言仍写 4，已更新为新的追加迁移数 5。代码审查发现继承模型字段顺序会使 Job 更新的 `$1` 不再是 job_id，已改为显式绑定身份、不依赖模型字段顺序；真实领取/发布/失败测试通过。没有降低断言或恢复旧语义。

最终稳定代码全量：`uv run --no-sync pytest -q --tb=short`，**1043 passed in 215.08s**。此前一次 **1033 passed in 216.07s** 运行期间继续补了 owner 命名空间与相关反例，不能用它替代最终回归；已重新完整运行。8 个改动 Python 文件 Ruff 与 format 检查通过，git diff --check 通过。

测试只创建并清理本轮 dr4a_test_* 数据库和 dr4a-test-* bucket；不应用迁移到用户数据库、不启动 Compose PostgreSQL、不改 .env、不触碰 docs/implementation。用户调试库尚未由本批命令升级；后续正常显式初始化/Runtime.prepare 才应用新迁移。只读 doctor 会准确将缺少当前迁移的库标为 schema 未就绪。

## 未完成

T040/T041 保持未勾选：creating/deleting 清理租约、cursor/CAS 与恢复扫描、完整 Job heartbeat/progress/cancel/retry、Service 组合/HTTP 公共视图仍待后续。Repository 的 activate 接受 App 已验证对象/索引的成功信号，本批不伪造这项真实依赖验收；实际 Ingestor/版本发布门与索引可读验证属 T044–T047。KB/内容/向量完整删除与迟到写入清理属 T049；真实检索回链与 PDF E2E 属 T048/T052。
