# T046：Standalone Milvus 索引验收

日期：2026-10-08。基线：`955b9d3` 加当前工作区改动；分支 `fix/research-api-contract-alignment`。全量测试包括尚未提交的搜索/trace 改动，不声称本次提交独立检出的全量结果。

## 实现与设计来源

依据 MODEL §5、API §7、FLOW §7、OPS §5.2，本批只验收索引 Adapter。

- `application/vector_models.py`：1024 维非零有限 dense、非空 sparse、UUID 版本 allowlist、文档/类型/年份过滤，不接受自由表达式。
- `application/ports.py::VectorIndexPort`：typed 索引契约。`verify_rows` 用 Strong 读回核验元数据及全部 dense/sparse 数值，供发布前验证；Service 负责生成经 PG 授权的范围。
- `infrastructure/vector/index.py::MilvusIndex`：固定 `dr4a_chunks_v1` collection、`kb_<UUID hex>` partition，稳定主键、动态字段关闭、nullable 页码/年份；dense COSINE/FLAT，sparse IP/SPARSE_INVERTED_INDEX。
- 双路 ANN 各最多 20 条，按实际 rank 做 RRF(k=60)，保留 dense/sparse rank，融合最多 20 条；不是 BM25，也不把分数当概率。
- 单线程有界执行器、整体 deadline、取消后 native 容量不提前释放；SDK 故障脱敏为 `index_not_ready`，不返回假空集合。
- 删除只作用于指定 KB/版本。范围不存在即清理完成，依赖故障仍报错；可重复 drop，不删除 collection。
- `milvus.py` 导出新 Adapter，旧 `MilvusStore` 保留给待迁移 legacy 调用。正式 KB Service 组合仍属 T042/T047/T048，不能称旧 dense-only 链路已经迁移。

## 真实依赖与资源隔离

使用已有 Compose `deep-research-milvus-1`；实测 server **2.6.0**、SDK **pymilvus 3.0.2**，pyproject/lock 精确固定。首次实测 collection 列表为空，本批创建目标 collection，没有覆盖已有数据。

每个测试只创建并精确清理自己的 UUID 分区；PG 沿用唯一测试数据库 fixture。没有新建服务容器，没有重启 Compose，也没有处理救援卷。collection 保留为后续正式索引，不进行 collection reset。

向量为显式受控 one-hot dense/token 权重，**不是 BGE-M3 模型验收**，T045 保持未完成。

## 反例与验证

先失败后修复：

1. typed Port 反例因缺少 `VectorIndexPort` 失败，补契约与 Adapter 注解后通过。
2. 真 Milvus：先删除本轮分区，再重复清版本，原实现抛 `partition not found`；增加存在性检查后通过。没有吞掉连接故障。

在 `backend/`：

```bash
UV_CACHE_DIR=/private/tmp/dr4a-uv-cache uv run --no-sync pytest -q --tb=short \
  tests/unit/test_vector_models.py tests/integration/test_mono_milvus.py \
  tests/integration/test_mono_kb_lifecycle.py tests/contract/test_mono_ports.py
```

**57 passed in 9.55s**，含 17 项索引模型/异步边界、10 项真 Milvus/PG 组合测试。Ruff check/format 通过。

真实覆盖：重复 ensure/upsert、Strong 读回与篡改拒绝；双路 rank/RRF 数值；PG staging 不可见、active 原子切换；向量已写但 PG 发布回滚后旧 active 仍可搜、新版本未公开；同向量跨分区不泄漏；删除不影响替换版本/其他 KB；null、三种块类型及年份/文档/profile 过滤；真实不可达端口报错而非零命中。

全量命令（包括真实 Milvus 测试，未 ignore 或 skip 本批）：

```bash
PATH=/Users/hoyishui/.local/share/fnm/node-versions/v24.13.1/installation/bin:$PATH \
  UV_CACHE_DIR=/private/tmp/dr4a-uv-cache uv run --no-sync pytest -q --tb=short
```

首次 **1 failed, 1185 passed in 245.65s**：`test_cli_trace` 要求 stderr 绝对为空，真实 Milvus 初始化后的 native gRPC 在 fork→exec 窗口写 `FD from fork parent still in poll list` 诊断；CLI 退出码、stdout JSON 和 trace 均正确。API §5 允许日志走 stderr，修正断言为成功退出、stdout 单 JSON、无 Traceback/trace_incomplete，仍检查 trace 事件完整性；未修改 CLI/SDK 的运行逻辑。真 Milvus＋trace 定向复验 **17 passed in 4.97s**。

再次全量 **1186 passed in 248.03s**。最后真实查询只剩 `_default` partition，测试分区均已清理；Compose 仍只有原来的五个 healthy 中间件，无遗留测试容器。`git diff --check` 通过。

## 后续边界

未包含 Parser→真实 BGE→入库 Worker→检索 Service→研究引用闭环。正文读取、重排、删除屏障再检属 T048，发布协调属 T047。PG-derived allowlist 组合测试不等于公网 Service 授权完成。研究工作流与 Prompt 调优按用户要求暂停推进。
