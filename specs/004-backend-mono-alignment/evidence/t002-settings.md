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
