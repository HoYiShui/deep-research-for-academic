# DR4A 后端运行与失败语义

> 状态：编写中。本文只记录知识库三个 App Service 的初步失败、恢复和运行规则。其他部分仍是骨架。本文不是当前权威设计来源。

## 1. 文档范围

本文定义失败语义、重试、幂等、恢复、一致性、并发限制和资源限制。本文不定义业务字段、接口签名或正常数据流。

## 2. 通用失败语义

> 待编写。本节将定义系统级错误分类、日志、指标和审计要求。

## 3. KnowledgeBase 管理

`KnowledgeBaseManagementService` 使用状态屏障执行删除：

1. 把 KnowledgeBase 标记为 `deleting`。
2. 拒绝新的入库和检索请求。
3. 取消或等待仍在运行的 IngestionJob。
4. 删除 Milvus 索引数据。
5. 删除 MinIO 对象。
6. 把 KnowledgeBase 标记为 `deleted`。

清理失败时，状态保持为 `deleting`。系统可以继续清理。系统不能提前返回 `deleted`。

## 4. 文档入库失败语义

### 4.1 可见性和部分写入

新 DocumentVersion 在入库期间保持 `staging`。只有以下步骤全部成功后，`DocumentIngestionService` 才把它提交为 `active`：

- 原始文件已经保存。
- 解析结果和 Chunk 正文已经保存。
- 必需的向量已经生成。
- Milvus 索引已经写入。
- PostgreSQL 元数据已经提交。

失败任务不能产生部分可见的检索结果。已写入的 staging 索引可以清理，也可以在重试时用稳定 Chunk 标识覆盖。

### 4.2 幂等

- 提交请求必须带幂等键。
- 服务端对幂等键设置唯一约束。
- 相同请求再次提交时，服务端返回已有 DocumentVersion 和 IngestionJob。
- 重试使用相同 DocumentVersion 和稳定 Chunk 标识。
- Milvus 写入必须是幂等 upsert。

初步内容身份使用 `kb_id`、`content_hash` 和 `ingestion_version`。最终键格式由实现设计确认。

### 4.3 重试和恢复

`DocumentIngestionService` 决定是否重试。`DocumentIngestor` 不执行无限重试。

| 失败位置 | 初步处理 |
|---|---|
| MinIO 写入 | 任务失败；可重试；版本不可见 |
| MinerU 解析 | 按错误类型决定是否可重试 |
| BGE-M3 Embedding | 有界退避重试；失败后任务进入 `failed` |
| Milvus upsert | 使用稳定 Chunk 标识重试；版本不可见 |
| PostgreSQL 提交 | 停止发布版本；恢复程序检查外部写入并继续提交或清理 |

运行中的任务需要租约或心跳。进程重启后，恢复程序查找租约过期的 `processing` 任务。恢复程序不能假定这些任务已经成功。

### 4.4 取消

取消是异步操作。执行组件在安全边界检查取消请求。系统进入 `cancelled` 前，必须停止新的写入并处理 staging 数据。

## 5. 在线检索失败语义

- KnowledgeBase 或 DocumentVersion 不可检索时，服务返回明确错误。
- Milvus 不可用时，服务返回依赖错误。服务不能返回空数组并声称检索成功。
- MinIO 正文读取失败时，服务不能返回没有正文的正常 `RetrievalResult`。
- Reranker 不可用时，系统可以按显式政策返回未重排结果。响应必须标记该降级。
- Research、API、CLI 和未来 Chat 使用相同的失败分类。

## 6. 并发和资源限制

- 同一幂等身份只能有一个有效入库任务。
- 同一 DocumentVersion 不能并发提交两次。
- 每个 KnowledgeBase 和整个进程都必须有入库并发上限。
- 文件大小、页数、Chunk 数量、Embedding 批大小和检索 `top_k` 必须有上限。
- 解析、Embedding 和索引写入需要独立超时。
- 具体数值必须通过部署容量测试确定。本文暂不固定数值。

## 7. 部署与可观测性

知识库运行至少需要 PostgreSQL、MinIO、Milvus 2.6、MinerU、BGE-M3 和 BGE Reranker。

每个 IngestionJob 至少记录以下信息：

- 当前步骤和状态。
- 尝试次数。
- 失败代码。
- 各步骤耗时。
- 文档、版本和 KnowledgeBase 标识。
- 写入的 Chunk 数量。

> 其他部署拓扑、健康检查、备份和容量规划仍待编写。

## 8. 相关文档

- 组件和 Port 的职责见 [后端架构设计](architecture.md)。
- 正常流程和状态机见 [后端数据流与状态设计](dataflow.md)。
- 实体字段和不变量见 [后端数据模型设计](data-model.md)。
- 输入、输出和错误映射见 [后端 API 契约](api-contract.md)。
