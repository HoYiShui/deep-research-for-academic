# DR4A 后端 API 契约

> 状态：编写中。本文只记录知识库三个 App Service 的初步公共契约和内部契约。其他部分仍是骨架。本文不是当前权威设计来源。

## 1. 文档范围与契约层次

本文定义跨组件边界的输入、输出和失败结果。契约分为三层：

1. HTTP 和 CLI 公共契约。
2. App Service 内部契约。
3. Port 内部契约。

字段语义由 [后端数据模型设计](data-model.md) 定义。本文不重复定义生命周期和恢复政策。

## 2. Research 公共契约

> 待收敛。当前 Research HTTP 契约仍见 [Research API 契约](../contracts/research-api.md)。

## 3. 知识库公共契约

以下路径是目标契约草案。实现前仍需确认认证、分页和错误码。

### 3.1 KnowledgeBase 管理

| 操作 | HTTP | 成功结果 |
|---|---|---|
| 创建 KnowledgeBase | `POST /knowledge-bases` | `201` 和 `KnowledgeBase` |
| 列出 KnowledgeBase | `GET /knowledge-bases` | `200` 和分页列表 |
| 读取 KnowledgeBase | `GET /knowledge-bases/{kb_id}` | `200` 和 `KnowledgeBase` |
| 更新 KnowledgeBase | `PATCH /knowledge-bases/{kb_id}` | `200` 和更新后的 `KnowledgeBase` |
| 删除 KnowledgeBase | `DELETE /knowledge-bases/{kb_id}` | `202` 和 `status: deleting` |

创建请求至少包含 `name`。它可以包含 `description`。删除是异步操作。

### 3.2 文档入库

| 操作 | HTTP | 成功结果 |
|---|---|---|
| 提交文档 | `POST /knowledge-bases/{kb_id}/documents` | `202` 和 `document_id`、`document_version_id`、`job_id`、`status` |
| 查询任务 | `GET /ingestion-jobs/{job_id}` | `200` 和 `IngestionJob` |
| 重试任务 | `POST /ingestion-jobs/{job_id}/retry` | `202` 和新的执行状态 |
| 取消任务 | `POST /ingestion-jobs/{job_id}/cancel` | `202` 和取消状态 |

提交文档使用文件输入和入库参数。请求必须提供幂等键。服务端不能根据客户端重试创建重复版本。

### 3.3 在线检索

`POST /knowledge-bases/{kb_id}/search` 接受以下逻辑输入：

- `query`
- 可选的 `document_ids`
- 可选的 metadata filter
- 可选的 `top_k`

成功响应返回 `RetrievalResult[]`。空数组表示检索成功但没有匹配项。依赖不可用不能伪装为空数组。

## 4. CLI 契约

CLI 与 HTTP 入口调用相同的 App Service。CLI 不通过 HTTP 访问本地后端模块。

知识库 CLI 需要覆盖以下用例：

- 创建、列出、读取和删除 KnowledgeBase。
- 提交文档并返回 `job_id`。
- 查询、重试和取消 IngestionJob。
- 执行检索并输出 `RetrievalResult`。

命令名、参数名和输出格式仍待确认。

## 5. 内部 App Service 契约

### 5.1 KnowledgeBaseManagementService

| 方法 | 输入 | 输出 |
|---|---|---|
| `create` | `owner_id`、名称、可选描述 | `KnowledgeBase` |
| `get` | `owner_id`、`kb_id` | `KnowledgeBase` |
| `list` | `owner_id`、分页参数 | KnowledgeBase 分页结果 |
| `update` | `owner_id`、`kb_id`、变更集 | `KnowledgeBase` |
| `delete` | `owner_id`、`kb_id` | 删除操作和当前状态 |

### 5.2 DocumentIngestionService

| 方法 | 输入 | 输出 |
|---|---|---|
| `submit` | `owner_id`、`kb_id`、文件引用、幂等键、入库参数 | `Document`、`DocumentVersion` 和 `IngestionJob` 的标识与状态 |
| `get_job` | `owner_id`、`job_id` | `IngestionJob` |
| `retry` | `owner_id`、`job_id` | 更新后的 `IngestionJob` |
| `cancel` | `owner_id`、`job_id` | 取消请求后的 `IngestionJob` |

### 5.3 KnowledgeRetrievalService

| 方法 | 输入 | 输出 |
|---|---|---|
| `retrieve` | `owner_id`、`kb_id`、查询、过滤条件和数量限制 | `RetrievalResult[]` |

`retrieve` 不返回 Research `Evidence`。调用者负责把 `RetrievalResult` 转换为自己的上层对象。

## 6. 内部 Port 契约

| Port | 最小能力 | 目标 Adapter |
|---|---|---|
| `KnowledgeBaseRepositoryPort` | 创建、读取、列出、更新状态 | PostgreSQL |
| `DocumentRepositoryPort` | 保存 Document 和 DocumentVersion；提交或废弃版本 | PostgreSQL |
| `IngestionJobRepositoryPort` | 创建任务；原子更新状态、进度和尝试次数 | PostgreSQL |
| `ContentStorePort` | 保存、读取和删除原始文件、解析结果与 Chunk 正文 | MinIO |
| `DocumentParserPort` | 把源文件转换为结构化文档 | MinerU |
| `EmbeddingPort` | 生成查询和文档向量 | BGE-M3 |
| `VectorIndexPort` | 幂等写入、删除和混合检索 Chunk 索引 | Milvus 2.6 |
| `RerankPort` | 对候选结果重新排序 | BGE Reranker |

Port 只暴露项目需要的能力。Port 不能泄漏外部 SDK 的请求或响应类型。

## 7. 通用失败响应

> 待编写。本节将统一 HTTP、CLI、App Service 和 Port 的错误类型映射。

## 8. 相关文档

- Service 和 Adapter 的职责见 [后端架构设计](architecture.md)。
- 状态转换见 [后端数据流与状态设计](dataflow.md)。
- 字段定义见 [后端数据模型设计](data-model.md)。
- 重试、恢复和降级见 [后端运行与失败语义](operations.md)。
