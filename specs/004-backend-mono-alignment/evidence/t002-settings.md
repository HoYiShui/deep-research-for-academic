# T002：统一 Settings

日期：2026-10-05；基于 `b95247b` 的实施增量。设计依据：API §1.1/§5，MODEL §3.2，OPS §2/§7。

## 实现

`backend/application/settings.py` 提供不修改进程环境的加载：默认值 < backend/.env < 导出变量 < 显式overrides。dotenv支持引号，禁止隐式变量插值；未知显式配置拒绝，OS无关环境变量忽略。HTTP startup与Container、CLI使用同一Settings；旧CLI env入口只为暂未迁移的命令保留setdefault兼容。

生产配置强制认证、足够长JWT secret、必需存储/远程模型凭据；预算预留与租约周期须一致，CORS不得通配，公开服务URL不得含凭据。SecretStr与隐藏ValidationError输入防止repr/JSON异常泄密。Run配置快照只包含非敏感版本、范围、预算/超时/并发。

组合根显式将配置传给LLM/搜索/PG/Milvus/Embedding/Rerank/Auth；LLM SDK隐藏重试关闭，避免叠加应用重试。配置本地LLM但无本地Adapter时明确拒绝，不偷用外部服务；生产无显式隔离执行Adapter时拒绝，不因配置标记而仍执行Docker。实际本地模型/生产Worker由后续T050/T057接通。

版本默认 `unconfigured` 是缺能力标记，不宣称模型已锁定或Parser已实现。后续冻结/能力检查必须拒绝不能执行的profile；真正权重/profile固定验收属于T045/T056/T057。

## 测试证据

先新增 `backend/tests/unit/test_settings.py`，运行时因Settings模块不存在失败（退出2）；再实现。

实测 `uv run pytest -q`：**145 passed in 2.75s**（原124 + 新21）。新增反例覆盖：优先级/引号、不改环境、非法预算/租约/空版本/CORS/未知字段、生产认证绕过（含实际TestClient startup）、缺凭据、快照脱敏、URL凭据拒绝、显式Container配置、本地模型不向外回退。

`uv run ruff check` 对所有本任务改动Python文件通过；另外对主要新增/修改文件 `--select E501` 通过（≤100字符）。`git diff --check`通过。

再次 `uv run python -m cli doctor --json`：退出0，env/model_weights/postgres/milvus/minio均true；仅连通性证据，不将其写成目标readiness或E2E。

仅更新 `.env.example`，没有改用户 `.env`。依赖python-dotenv显式写入pyproject并同步uv.lock；无业务数据库写入。

T002完成；T003及后续未完成。生产完整API认证/CSRF、typed Pipeline Schema与真正沙箱等不在本任务通过声明中。

## 2026-10-07：最低研究调试诊断（T056部分）

`cli doctor`现在通过Settings读取配置，不把`.env`复制到进程环境；支持`--scope research`跳过当前暂缓的KB基础设施，`--debug-db`只选择已存在的匿名开发数据库。默认all保留Milvus连接与本地模型文件探测；Hub名称/空目录不算已准备模型，不下载或推理。匿名开发配置不要求JWT，认证profile要求JWT。

PG连接探针使用5s连接/命令超时、readonly事务并关闭连接，明确区分连接与Schema。Schema对照当前仓库SQL迁移集合，拒绝缺失/未知版本，核对必要Run/调用账本/快照表；不执行任何DDL或迁移。这里仅检查迁移标记/表，不声称全部约束/权限验证。MinIO使用配置的TLS设置及凭据探测已存在bucket，短连接/读取超时、关闭SDK transport，不建bucket/对象。Milvus同步SDK探针移到线程并关闭client，拒绝Lite路径。

JSON保留逐项checks，附scope与limitations；失败提供标准service_not_ready error、request_id及非零退出码。limitations明确模型推理、Parser、写权限、完整Pipeline/报告质量未通过；当前注册worker范围只是实现说明，不是远程服务能力探测。

反例先失败：旧实现接受空白环境变量、Hub名称模型，以及没有分scope/Schema诊断。实施后的`test_cli_doctor.py test_cli_output.py test_mono_cli_dump.py`：21 passed in 5.32s。实际隔离PG覆盖空库/0001/当前完整迁移/未知版本且探针不建表/用户/会话；真实唯一MinIO bucket覆盖存在/不存在且探针后零对象。修复既有dotenv单测直接修改os.environ造成的测试顺序污染，未修改项目`.env`。

真实CLI：`uv run --no-sync python -m cli doctor --scope research --debug-db --json`退出0，env/postgres/postgres_schema/minio_bucket均true。相同命令去掉debug-db，连接恢复业务库成功但postgres_schema=false，退出3；不迁移或写入恢复库。没有收费模型/搜索调用。Ruff与diff检查通过；本批不称全量回归或完整T056完成。
