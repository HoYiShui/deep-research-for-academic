# T021部分进展：正式Checkpoint读取

日期：2026-10-06。设计依据：mono API §5、MODEL §3、FLOW §4。T021保持未完成，run/phase仍待切换；不会把旧CLI fake报告称为mono验收。

## 实现

`application/research_queries.py::checkpoint_view` 在同一事务锁住owner Session，读取它的当前Run及精确checkpoint_seq，校验phase、归属、冻结Brief和config一致。`cli/commands/dump.py` 使用共享Settings/正式PostgresResearchStore；移除旧PostgresStateStore和最高phase倒序查找。

开发默认固定owner；`--owner UUID`指定已有用户，production必须显式指定。dump不创建用户、不执行迁移、不领取Run、不启动模型、不改业务事实。有会话而无Run为checkpoint_not_found，其他owner会话与不存在会话均session_not_found。成功state为完整mono PipelineState，并返回checkpoint_seq。只读命令不能伪装将旧schema自动升级；旧schema返回退出码3/schema_incompatible且不重试。

`output.py`成功JSON有error:null；入口UsageError/AppError/AdapterError/未知异常均形成标准Error对象、单个stdout JSON。stderr诊断仅输出status/code；未知外部异常正文不泄露。此批不涵盖argparse自身的所有参数错误JSON化、旧worker的verbose正文日志或signals，因此完整CLI输出安全仍待后续T021收尾。

## 实测

`test_mono_cli_dump.py`使用真正CLI子进程、当前解释器及每例唯一真实PG数据库：

- 提交seq=2 research、seq=3 analyze、seq=4 research；dump返回seq4而不是较晚阶段的seq3，完整state一致。
- Dump前后Session.revision/status、Checkpoint数、Report数不变。
- 未冻结会话与不存在会话错误不同；既有其他owner无法读取，未知owner明确失败。
- 旧0001 schema返回schema_incompatible，迁移记录仍一条，无research_runs表。

统一错误测试验证秘密样例SDK异常不出现在stdout/stderr。最终定向 `test_mono_cli_dump.py test_cli_dump.py test_cli_output.py`：**10 passed in 4.36s**。补充旧schema前置之前全量 **761 passed in 84.02s**；补充后定向验证通过，未把该全量结果误称最新版本全部762项已重跑。Ruff/format/diff检查通过。

首次全量发现MinIO未运行，8个fixture连接拒绝；主动中断该轮（310passed/8errors），单独启动现有deep-research-minio-1，通过其ready探针，再完整回归。没有启动旧Compose PostgreSQL或修改恢复库。

当前恢复业务库直接运行dump返回schema_incompatible：它恢复的是旧业务schema，数据库连接恢复不等于mono迁移完成。没有修改其8个Session、3个Report等历史数据。后续正式写入口需执行已有保全迁移流程并验证历史备份，或继续使用独立开发数据库。

用户文件docs/implementation/未动，未修改.env或代理。此批不解决Compose接管恢复PG卷。没有push。
