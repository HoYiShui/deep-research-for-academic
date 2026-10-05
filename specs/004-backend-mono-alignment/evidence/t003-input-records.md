# T003 部分证据：严格输入及持久化记录

日期：2026-10-05。基于提交 `f26d675` 实施。本记录不是 T003 完成声明，也不是业务 E2E。

后续状态：本首批的未完成类型已由 [完整快照证据](t003-checkpoints.md) 补齐；下文记录的是首批提交时的边界，不覆盖后续验证。

## 实现与设计来源

- MODEL §1–3、OPS §1：`backend/domain/research/models.py` 实现十字段 ResearchBrief、PartialResearchBrief、SourceSelection、ClarifyAssessment、SessionState、Message、BriefRecord、ResearchRun、RunConfig、Failure。`state.py` 导出这些契约；没有建立另一个 Service 或事实库。
- UUID 身份、严格字符串/整数/布尔、未知字段拒绝、UTC aware 时间、状态/phase 闭集、字段上限、KB 类别与 IDs 绑定、固定版本清单及预算 reserve 校验。
- 冻结 Brief 要求完整字段、确认身份、冻结时间与正确 SHA-256；不能把部分 draft 当冻结记录。ResearchBrief 的字段赋值禁止修改。
- `ids.py` 将短 MD5/分隔符拼接替换为规范 JSON + 全长 SHA-256；保持 list 顺序、对象键排序和类型区别，不接受 NaN/Infinity。旧 Scout 等调用开始使用新算法，但其自然键/取证质量仍须在 T027 修正。
- `Settings.run_config_snapshot()` 已调用严格 RunConfig 校验，不能生成仅选 KB 类别却没有 UUID 的配置；citation_depth/gap_queries_per_spec 按 MODEL 的正整数约束调整，不再接受 0。

## 反例与验证

确定性测试，无真实模型调用、无数据库或对象写入。

1. 先新增 `tests/unit/test_research_models.py` 并运行，因不存在 canonical_hash/严格类型而 collection 失败（ImportError），再实现。
2. 新增 Session JSON 往返断言后失败：PartialResearchBrief 默认序列化补齐 null，重新读取被拒绝。修复为只序列化实际出现的字段；缺失字段不再存 null。
3. 覆盖 Brief string/list 错型、空字段、未知字段、未支持任务类型、字段长度、显式 null、来源组合、稳定 hash 歧义、冻结内容/hash 错配、naive 时间、非法状态、版本/seq/计数类型、版本清单不全、预算越界及 Failure 嵌套/非有限诊断。
4. `cd backend && uv run pytest -q`：**163 passed，2.72s**（此前基线 145）；新增 18 个测试案例。
5. 修改文件的 Ruff 与 `git diff --check` 在提交前复核；实际结果以本次提交命令输出为准。

## 未完成边界

T003 保持未勾选：Checkpoint 与完整严格 PipelineState 还未迁移；旧 Orchestrator/CLI 仍使用 dataclass PipelineState。不能用新增输入类型宣称完整快照契约已满足。后续需迁移调用方并补完整快照 hash/身份一致性测试，详细研究事实 Schema 按 T024/T027/T030/T034 实施。

本轮未运行 HTTP/PG 事务验证，未修改用户 `.env`，未清理或提交 `docs/implementation/`。
