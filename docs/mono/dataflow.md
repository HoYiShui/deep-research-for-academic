# DR4A 后端数据流与状态设计

> 状态：编写中。本文只记录知识库三个 App Service 的初步数据流和生命周期。其他部分仍是骨架。本文不是当前权威设计来源。

## 1. 文档范围与阅读规则

本文定义数据怎样移动，以及状态怎样变化。本文不重复定义模块职责、对象字段、方法签名和重试参数。

每张图的箭头必须说明调用或数据移动。状态图只显示状态转换。

## 2. Research 会话与 Pipeline

> 待编写。本节将记录 Clarify、Brief 冻结和 Research Pipeline 的数据流。

## 3. 知识库数据流

### 3.1 KnowledgeBase 管理

`KnowledgeBaseManagementService` 管理 KnowledgeBase 元数据和生命周期。创建请求先写入 PostgreSQL。删除请求先把 KnowledgeBase 标记为 `deleting`。系统完成索引和对象清理后，才把它标记为 `deleted`。

```mermaid
flowchart LR
    Caller[API 或 CLI] -->|创建、更新或删除请求| Service[KnowledgeBaseManagementService]
    Service -->|读写元数据和状态| Metadata[(PostgreSQL)]
    Service -->|删除向量索引| Vector[(Milvus)]
    Service -->|删除文档对象| Content[(MinIO)]
    Service -->|返回当前状态| Caller
```

*图 1　KnowledgeBase 管理数据流。箭头表示用例调用或数据移动。*

### 3.2 文档入库

`DocumentIngestionService` 是入库用例的控制者。`DocumentIngestor` 只执行一次入库尝试。

```mermaid
flowchart LR
    Caller[API 或 CLI] -->|文件和入库参数| Service[DocumentIngestionService]
    Service -->|保存原始文件| MinIO[(MinIO)]
    Service -->|创建 DocumentVersion 和 IngestionJob| PostgreSQL[(PostgreSQL)]
    Service -->|执行一次入库尝试| Ingestor[DocumentIngestor]
    Ingestor -->|读取原始文件| MinIO
    Ingestor -->|解析文件| Parser[MinerU Adapter]
    Parser -->|结构化文档| Ingestor
    Ingestor -->|保存解析结果和 Chunk 正文| MinIO
    Ingestor -->|生成向量| Embedding[BGE-M3 Adapter]
    Ingestor -->|写入 dense、sparse 和元数据索引| Milvus[(Milvus 2.6)]
    Ingestor -->|返回本次执行结果| Service
    Service -->|提交可见版本并更新任务状态| PostgreSQL
    Service -->|返回 job_id 和状态| Caller
```

*图 2　文档入库数据流。箭头表示调用、数据移动或结果返回。*

入库使用以下可见性规则：

- 原始文件保存成功后，系统才创建可执行任务。
- `DocumentIngestor` 完成解析、切片、Embedding 和索引写入后，返回执行结果。
- `DocumentIngestionService` 完成元数据提交后，才把新版本标记为可检索。
- 部分写入不能使未完成版本进入检索结果。

### 3.3 在线检索

`KnowledgeRetrievalService` 为 Research、API、CLI 和未来 Chat 提供一个检索入口。未来 Chat 不是当前功能。

```mermaid
flowchart LR
    Consumer[Research、API、CLI<br/>或未来 Chat] -->|查询、范围和过滤条件| Service[KnowledgeRetrievalService]
    Service -->|检查 KnowledgeBase 和版本状态| PostgreSQL[(PostgreSQL)]
    Service -->|查询规范化和向量生成| Embedding[BGE-M3 Adapter]
    Embedding -->|查询向量| Service
    Service -->|dense、sparse/BM25 和 metadata filter| Milvus[(Milvus 2.6)]
    Milvus -->|候选 Chunk| Service
    Service -->|候选重排| Reranker[BGE Reranker Adapter]
    Reranker -->|重排结果| Service
    Service -->|读取 Chunk 正文| MinIO[(MinIO)]
    Service -->|RetrievalResult| Consumer
```

*图 3　知识库在线检索数据流。箭头表示调用或数据移动。*

`KnowledgeRetrievalService` 返回中立的 `RetrievalResult`。Research 将该结果转换为 `Evidence`。未来 Chat 可以把它转换为上下文或引用。

## 4. 知识库生命周期

### 4.1 KnowledgeBase 状态

```mermaid
stateDiagram-v2
    [*] --> creating
    creating --> active: 元数据创建成功
    creating --> deleted: 创建失败并完成清理
    active --> deleting: 接受删除请求
    deleting --> deleted: 索引和对象清理完成
    deleted --> [*]
```

*图 4　KnowledgeBase 生命周期。箭头表示允许的状态转换。*

处于 `deleting` 或 `deleted` 状态的 KnowledgeBase 不接受新的入库和检索请求。

### 4.2 IngestionJob 状态

```mermaid
stateDiagram-v2
    [*] --> accepted
    accepted --> processing: 执行开始
    accepted --> cancelled: 执行前取消
    processing --> completed: 版本提交成功
    processing --> failed: 本次尝试失败
    processing --> cancelled: 取消完成
    failed --> processing: 接受重试
    completed --> [*]
    cancelled --> [*]
```

*图 5　IngestionJob 生命周期。箭头表示允许的状态转换。*

`DocumentIngestionService` 是此状态机的控制者。`DocumentIngestor` 返回执行结果，但不决定重试、取消或最终状态。

## 5. 其他数据流

> 待编写。本节将记录认证、报告读取、取消和其他跨模块数据流。

## 6. 相关文档

- 模块职责和依赖方向见 [后端架构设计](architecture.md)。
- 实体字段和关系见 [后端数据模型设计](data-model.md)。
- 输入和输出见 [后端 API 契约](api-contract.md)。
- 失败、重试和恢复见 [后端运行与失败语义](operations.md)。
