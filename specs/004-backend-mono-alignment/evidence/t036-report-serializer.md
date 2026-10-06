# T036：确定性报告装配与发布校验

2026-10-06。设计来源：MODEL §4.4–4.5、FLOW §3.4/§4、API §2.5/附件约定、OPS §1/§3.1。实现入口为 `application/report_serializer.py`；纯装配与校验在 `domain/research/reporting.py`，不调用模型、网络或数据库。`ReportPublisher.publish` 将其结果交给已验证的四事实 PG 事务；Repository 再次确定性装配核对，不能绕过服务层伪造 Markdown。

## 已实现

- 固定 0–5 + References 及必需子节；0 从冻结十字段 Brief 投影，第 3 节根据三种 TaskPayload 渲染候选问题卡、方法差分矩阵或协议—指标—结论映射，保留不能支持的结论与失败模式。
- 每章节正文必须等于同版 Statements 的确定性文本投影；章节不得为空。第 3 节每行定位到该章已登记 Statement，各字段及总结必须在登记文本中；不能在审核后拼接新任务模块或自由正文。显式候选数量支持常用中文/英文数字、至少/至多/恰好；不能解析的明确中文数量要求先在 Clarify 规范化，不猜一个默认值。
- 每事实 Statement 必须有非空 Claim/Evidence 绑定及对应关系，Claim 类型/状态与计划 Spec/信源要求一致。引用需登记正文对象/hash、provenance 与 Location，拒绝 snippet/abstract 发现材料冒充原文。私有引用必须归属冻结知识版本。内容是否确由真实 Fetch/Parser 获得、摘录是否支持论断仍由 T026/T027/T035 证明，结构校验不替代它们。
- 引用计算只接受 completed、compatible、符合计划和固定 template_version 的 Artifact；验证 Metric→Observation→Evidence 回链与已知条件，不允许未知条件相等、漏原文或混单位计算。附件只生成 owner 授权 API 路径，basename/扩展名白名单，拒绝穿越和 HTML/SVG 等活动文件；不暴露对象 key 或公开 MinIO 地址。真实沙箱执行与附件读取分别属于 T032/T037，fixture 的 completed 不冒称真实沙箱证明。
- References 仅来自真正绑定的 Evidence，按 source_id 稳定编号，保留版本/页码/表格等定位；无引用明确写“无可核验引用”。未使用的全局 Source 不自动进入书目。
- 风险从 Gap、未解决事项、Critic 未解决问题、未闭环 Claim、未证实的建议/假设/限制、比较或分析失败派生；不接受任意模型风险列表替换。最终收缩未完成分析也披露。审核 verdict 保持原值；不足报告不得升级 approved。
- 不受信文本先 HTML 转义并禁止其产生 Markdown 结构/链接；只有代码生成 anchor、引用及附件链接。引用 URL 只允许无凭据的 HTTP(S)。报告按实现的 2MiB 上限拒绝超长交付，不截断丢失断言。
- Report、done Checkpoint、Run/Session.completed 同事务。任何一处写入失败均回滚；Repository 拒绝篡改正文、书目、标题或删除风险，即使 FinalReport Schema 合法。

## 验证与边界

`tests/contract/test_mono_report.py`：**51 passed in 0.20s**。覆盖三任务/固定结构、空引用、稳定书目、事实与任务行漏绑定、未登记正文、非法链接与 HTML/Markdown/anchor 注入、候选数量、旧版本/悬空 ID、附件、分析链与最终收缩风险、禁止静默截断。

最初组合目标集（报告 contract/真实 PG 发布/真实 PG-MinIO Driver/独立 SIGKILL）**83 passed in 14.96s**；随后补充数量约束与 HTTP 风险核对，最终结果记录在下方。Driver 的三任务 fixture 已改用生产 ReportPublisher，不再使用裸 FinalReport 事务 fixture；业务模型/取证仍是明确受控输出。

过程中一项 Runner 测试曾把 PG completed 和稍后完成投影当作同一时刻，已等待执行任务结束再核对投影；一项旧 HTTP 测试将风险硬编码为空，已核对真实发布列表。曾使用 pytest 保留参数名 request 导致 collection 失败，已改为 deliverable。失败批次均不计为验收通过。

T036 的装配/校验/发布范围与三任务真实业务报告验收分开：T034/T035 真实 Writer/Critic、T039 活 HTTP+真实模型搜索原文报告及 T037 附件读取仍未完成，不能据此宣布 M4 或整个 goal 完成。原数据库和无关文件不修改；所有 PG/MinIO 资源使用隔离测试身份。

最终组合目标集（含 HTTP Clarify/Report 路由）**119 passed in 25.91s**；最终完整回归 **607 passed in 82.31s**。本轮修改文件 ruff 检查和 format 检查、`git diff --check` 通过。T036 勾选仅代表本页明确列出的装配/校验/PG 发布范围，未将真实业务或未实现的附件读取计入。
