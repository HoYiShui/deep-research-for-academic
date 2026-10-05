# T009–T010 严格 Clarify 与纯会话候选

2026-10-05。设计来源：FLOW §2.2、MODEL §2、API §2.1–2.4。仅完成worker/纯候选底座；**不是M1或真实HTTP业务E2E**。

## 实现

- canonical `architect.clarify` 返回严格 ClarifyAssessment，不挑选status、不访问存储。JSON只接受对象与完整Schema，拒绝未知字段、重复key、非法task_type、缺字段、错型、空字段、超过两个问题；不把脏输出转为空成功。整体输出≤64000字符，各问题/假设/字段解释≤8000；query/answer≤16000，模型超时受控，取消向上传播。
- `machine.assess_brief` 合并类型化patch，结合字段缺失与missing_fields/field_reasons。task_type/decision_goal/research_object/deliverable不默认猜测；scope/comparison_scope/claims_to_verify/evidence_requirements/conclusion_boundary只在未标注关键语义缺口且缺项时补保守默认，完整文字写进assumptions。最终十字段校验后只能confirm，不能ready/冻结。
- 非空scope但存在field_reasons仍ask；无patch且模型声称完整仍不能越过缺少的核心字段。ask总有1–2个问题，confirm清空questions/missing_fields。
- canonical `SessionService` 只依赖LLM/Clock/ID factory，返回SessionChange或ValidatedFrozenInput；无Repository、无保存、无spawn。初始revision/version=1、round=0；后续成功候选revision/version/round递增；生成两条连续序号审计消息和未冻结Brief。
- 初始模型判断独立于最多3次后续自动模型轮次。上限后不再调用模型；未提供明确brief_patch保持ask，提供字段补充只清除已明确补充的缺口，其他语义缺口仍保留。完整候选仍待用户确认。
- 退回固定ask；上限后保留草稿与反馈、不调用模型，要求明确patch。确认校验不调用模型、不创建Run；过期version/错误status/残留缺口拒绝。
- 历史只把最后8条、每条最多8000字符送给模型，保存的输入/审计消息不截断修改；sources由调用用例提供，不允许模型改KB身份。实际授权/隐私前置归T011/T050/T054，未以prompt代替安全边界。
- 合并假设超出Brief边界时转为明确model_output_invalid（模型分支）或invalid_brief（显式patch分支），不截断隐去披露或把Pydantic内部错误泄漏为成功。

## 反例与命令

新增worker测试首次因缺assess_brief而失败；补实现后通过。合并8000字符模型假设与默认披露的反例实际暴露了未封装ValidationError，修正后明确拒绝且旧状态不变。

在backend目录：

```bash
uv run pytest -q tests/unit/test_session.py tests/unit/test_clarify.py tests/unit/test_mono_clarify.py
uv run pytest -q
```

- 定向 **30 passed in 0.08s**：非法/伪造输出、完整默认披露、语义缺口、初始ask/confirm、3轮上限、显式patch、退回、确认版本/状态、原状态不变、假设总长、历史有界、模型失败/超时/取消。
- 模型是受控Model/FakeLLM、时钟FakeClock，未调用真实收费模型；没有声称语义模型的真实准确率。
- 修改路径ruff All checks passed；git diff --check通过。
- 提交前最后全量回归：**268 passed in 7.28s**，包括既有真实隔离PG测试。不能将其中的旧fake quickstart计作目标HTTP业务通过。

## 接入边界与下一步

T007的剩余默认HTTP组合根需要新会话用例，因此先完成本批纯候选依赖。T008 HTTP反例仍需先建立，再做T011/T012接入与T013真实模型会话。不可用本批单测跳过HTTP/事务/幂等/来源授权验收。

旧HTTP/CLI装配暂时明确使用LegacySessionService、legacy_clarify、legacy_decide_status；旧fake quickstart仍属于预mono回归，不证明目标Clarify。它们必须在T011/T012/T021调用路径切换时移除，不能把新候选只留在单测、长期保留另一套可写会话事实。默认会话GET仍需mono存储装配，当前503边界不变。

本批未写/迁移用户数据库、未修改.env或无关docs/implementation、未调用外部收费模型；完整回归沿用隔离PG fixture。未推送。
