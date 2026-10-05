# T003：严格输入、身份与完整快照契约

日期：2026-10-05；续接 `9152e62` 的输入记录实现。验证为确定性测试，不是 HTTP/真实业务 E2E。

## 本次落点与设计映射

- MODEL §1–3、OPS §1：`backend/domain/research/state.py` 为 Pydantic 完整 PipelineState/Checkpoint/RunMetadata/BudgetUsage/UnitResult/VersionReference/Degradation；持久读取要求全部 27 个顶层字段，只有 initial 工厂填充文档规定的空输出。
- 校验 UUID、正整数版本/seq、严格 schema_version=1（拒绝 bool）、冻结 Brief hash、Run 来源与冻结选择一致、已用预算与上限、知识版本范围、manifest 键与 unit_id。
- Checkpoint 核对 run_id、下一 phase 和完整 state hash；模型实例再验证，嵌套可变集合被改坏也不能绕过持久入口。
- 事实集合不使用 Any：`facts.py` 提供 MODEL §4 的结构类型、Location、十进制字符串、受限属性对象、五类参数与输出结构、TaskPayload 判别联合。未知字段拒绝；Artifact output 按 operation 校验。这里只完成结构底座，不声明 Agent 取证、可比性、执行、审核或报告交付已完成。
- State 校验来源→证据→观察→指标→比较组链，关系三元组去重，Artifact 输入链，草稿/Binding 版本与定位、发布报告身份。非空计划必须恰有五章，至少一个 ClaimSpec/问题；空计划不能推进到 research。
- 旧 dataclass 放到明确命名的 `legacy_state.py`；旧 Orchestrator/CLI 与旧持久化集成 fixture 显式 import 它。没有将旧宽松快照自动转换成 mono-v1。T017/T021 必须移除该临时路径；T005 处理旧持久记录隔离。没有建立两个新的可写事实库。

## 测试与失败证据

先写新快照反例，因 Checkpoint 不存在而 collection 失败；实现后全部必填键、hash/Run/phase 错配、未知/错型事实与预算、非空链、JSON 往返通过。

补 nested revalidation 后发现 PartialResearchBrief 实例被补 null 而拒绝；新增实例再验证反例复现失败，再用 wrap validator 保留字段子集，测试恢复通过。

- `cd backend && uv run pytest -q`：**182 passed，2.62s**。
- 修改文件 Ruff：**All checks passed**。
- `git diff --check`：通过。
- `tests/unit/test_state.py` 已替换废弃的“空 PipelineState 合法”断言，保留 WORKERS 既有 dispatch 契约检查。
- `tests/unit/test_research_models.py` 覆盖输入/Session/Run；`test_state.py` 覆盖完整快照；`test_fact_shapes.py` 覆盖五 operation 参数/输出的未知字段、定位和数字约束。

## 完成与剩余边界

T003 的核心 Schema/枚举/稳定 ID 和完整快照类型验收已完成。Service 与 Repository 尚未接入；不能由这一勾选推断当前 HTTP 后端已经采用目标 API。后续按 T004–T007 接存储/接口，T017/T021 改 Orchestrator/CLI。

事实原文摘录范围/自然键、引用全面性、数值条件兼容、模板执行结果真实性、全段落审核/报告固定章节等行为仍分别由 T024/T027/T030/T031/T033–T036 验收；本次 fixture 不证明这些业务要求。预算跨 checkpoint 不重置须 T015/T020 的真实事务证明，当前只校验单个快照的类型及上限。

没有调用收费模型、没有写现有 PG/MinIO/Milvus，未改 `.env`，未清理 `docs/implementation/`，未推送。
