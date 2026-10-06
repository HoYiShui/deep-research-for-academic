# T020 工具结果内容缓存（部分）

## 已实现与验证

- `ResultCachePort` 是工具结果专用的窄接口，不宣称文档入库 ContentStore 生命周期已完成。
- `MinioResultCache` 使用真实 MinIO SDK（锁定 `minio==7.2.20`）。对象地址为两段调用命名空间加内容 SHA-256；相同地址只对应相同字节，不接受任意可覆盖对象名。
- 每次读取校验完整字节长度及 SHA-256，不把 ETag 当正文 hash。重复写先检查已有内容；已有对象损坏时拒绝覆盖。缺失、损坏、上游故障分别失败，合法空字节可以缓存。
- 单对象上限 10 MiB；两个专用线程与两个 I/O 槽，取消调用者不会提前释放仍在执行的线程槽。SDK 无隐式网络重试；连接/读取有超时。取消不能撤回已经发给 S3 的请求，调用账本仍须处理 uncertain 窗口。
- 不静默创建应用 bucket；部署负责创建和授权。测试仅创建并删除本次唯一的 `dr4a-test-<uuid>` bucket，不触碰应用数据。
- 旧 `MinioContentStore.get()` 空返回已改为明确 `content_store_not_configured`，不能再用空字符串冒充成功。

2026-10-06：真实 MinIO 集成 2 项通过（包含六个并发相同写入、另一客户端读回、对象数量核对、空/缺失/人为损坏检测）；单元/契约与集成合计 **15 passed in 0.23s**。并发取消槽测试使用受控线程，不冒充真实网络取消。

最终全量 **422 passed in 52.68s**，包含取消槽测试及线程所有权修正。此前阶段全量 **409 passed in 53.96s**。

SDK 接口依据：[MinIO 官方 Python SDK API](https://github.com/minio/minio-py/blob/master/docs/API.md)。

## 尚未证明

T020 仍未完成：尚缺 ToolCallRecord/预算预留事务、成功对象与 PG 调用账本关联、uncertain 的只读重放记录、恢复不重发成功调用，以及并发预算测试。默认 HTTP Runner 尚未启动正式 Pipeline。这些对象测试不证明付费工具缓存或真实研究报告 E2E。
