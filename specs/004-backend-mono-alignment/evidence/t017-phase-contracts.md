# T017：阶段输入切片与受控合并（部分完成）

2026-10-06。设计依据 API §6 的确切 PhaseResult 六字段与阶段写白名单、MODEL §4.1 的计划例外、FLOW §3.1/§3.4。

实现 `domain/research/phase_contracts.py`：PhaseInput 仅含该阶段允许读字段与 rework_targets，不包含可写全局State、Session/Run/租约/预算。稳定 semantic hash 不纳入预算、elapsed、租约或随机事件时间。PhaseResult 严格六字段；changes 从完整 State 的对应严格类型推导验证，保留 StrictInt 等 metadata，禁止未知控制字段。结果归属 phase/input_hash 必须匹配当前切片。

纯合并：plan 五章整体替换、禁止空计划；依据 MODEL，纯建议/风险章可无额外查询、复用相同 ClaimSpec，但同ID不同定义拒绝。research 稳定集合/关系追加去重、相同原文ID不能换quote/hash，Claim只能重算status/reason，Source合并provenance。analyze 替换目标requirement派生项，保留其它组共享Metric；影响已写章节时清目标草稿/Binding与旧review投影，写明rework范围，不把旧数字继续当有效结论。write 只改目标章，未改章及绑定一起升全局version；清过期review。review保留issue身份，复核需说明与当前version，新issue必须指向存在对象，不改草稿。

所有合并重新构造完整 PipelineState 并校验回链/覆盖；不推进phase、不花预算、不写unit_manifest、不创建report、不存数据库、不触发其它阶段。结构/身份通过不等于语义审核或原文取证通过。

27项确定性正反例通过；全量388 passed，53.84s。包括空/缺章/冲突计划、控制字段越权、旧input hash、严格primitive类型、局部写作版本、反馈身份与未知目标、Coverage回链、无分析需求skip、requirement范围替换与共享Metric保护、只失效目标章节、相同Evidence ID改quote/hash拒绝。Ruff/diff检查通过。

未完成：正式 execute_phase 调度、实际Worker、逐单元manifest/快照、Machine推进/返工、默认Runner组合。T017不勾选；CLI尚不使用本契约，不能称阶段CLI迁移完成。
