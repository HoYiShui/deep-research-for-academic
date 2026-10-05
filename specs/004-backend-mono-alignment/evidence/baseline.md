# T001：实施前基线

日期：2026-10-05；实施起点：`b95247b`；分支：`fix/research-api-contract-alignment`。

## 命令与实测

在 `backend/` 执行 `uv run pytest -q`：**124 passed in 3.16s**，退出0。这是当前旧实现的回归基线，不证明 mono-v1 或真实业务 E2E。

`uv run python -m cli --help`：退出0，命令为 doctor/run/phase/dump/ingest/search；未提供目标中的 kb/job 子命令。

首次 `uv run python -m cli doctor --json`：退出3，env/model_weights=true，postgres/milvus/minio=false。同次 `docker info` 表明 Docker daemon 未运行。

执行 `docker desktop start`，然后根目录 `docker compose --env-file backend/.env up -d`，复用已有容器和卷。再次 doctor：退出0，五项均true。`docker compose --env-file backend/.env ps --format '{{.Service}} {{.State}} {{.Health}}'`：postgres/minio/etcd/minio-milvus/milvus 均 running healthy；Docker Server 29.7.2。没有删除卷、重建数据库或运行旧 smoke 的收费模型链。

## 验证覆盖审计

- `tests/integration/test_quickstart.py`、`test_slice_persistence.py` 使用 FakeStateStore 与受控模型；文件名 integration 不等于真实 PG/模型。
- `test_slice_kb_ingest.py` 使用 FakeParser/FakeEmbedding/内存文档登记；`test_slice_kb_search.py` 的向量/重排为fake。不能证明真实PDF或Milvus写读。
- `scripts/smoke_e2e.py` 直接构造 Container、调用Service且混用FakeRetrieval/FakeExecution/内存用户；仍硬编码PG5433、按旧ready语义启动。未运行；不能作为活HTTP或all-real证据。
- `scripts/smoke_real.py` 也残留硬编码PG5433。后续T059更新，不在基线阶段贸然跑用户库迁移。
- `infrastructure/parser/pdf.py` 的MinerUParser返回空text/tables/formulas；MinioContentStore.get返回空字符串，仍是存根。
- `infrastructure/embedding/bge_m3.py` 使用通用SentenceTransformer.encode的特定dense/sparse参数；doctor没有加载模型验证这些能力。
- doctor的model_weights只接受HF名称/检查本地目录存在；PG只是连接，MinIO只是health/live，Milvus只是list_collections。green不证明schema、正文、真实sparse、Parser或执行器。
- 实施起点HTTP start返回status=clarify；owner没有进入研究Service，status按phase倒序恢复。目标行为由后续任务实现，不保留为新契约。

## 资源保护

原有 `docs/implementation/` 保持未跟踪、未修改。未读取输出 `.env` 原文或凭据值；未向外部模型发送资料。后续真实数据库迁移验证须使用新建的隔离测试数据库，并先验证旧记录的备份/隔离策略。

T001完成：基线命令已实测、fake/存根/真实连通性已区分。下一项T002，不将124个旧测试作为A1–A13通过证据。
