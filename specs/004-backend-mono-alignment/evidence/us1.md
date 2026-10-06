# US1：活 HTTP 澄清与真实模型冻结验证

2026-10-06。代码基线：`abc1aab` 加本次 T013/Clarify 修复（本证据随代码同批提交）。设计来源 API §2、FLOW §2、OPS §1–3。

## 工具与确定性验证

新增 `backend/scripts/verify_clarify_http.py`，仅经 httpx 调用公开 HTTP；不调用 Service、Repository 或直接写 PG。query/session 二选一；answers 文件只能含合法 MessageInput 的内容/patch/sources，版本从最新 GET 获取；approve 文件必须与当前 confirm 的 session_id、brief_version、完整十字段逐项相等。工具不从模型响应自行生成批准，不默认同意假设；缺批准时正常停止在 ask/confirm，绝不建 Run。stdout 一个 JSON；输出包含公开测试文本，不能拿私有材料随意运行或保存日志。

在 backend 的用法（自行填入活后端 origin 与当前会话ID）：

```bash
uv run python -m scripts.verify_clarify_http --url http://127.0.0.1:8000 --query '我想研究入侵检测。' --model-mode real
uv run python -m scripts.verify_clarify_http --session SESSION_UUID --answers-file answers.json --model-mode real
uv run python -m scripts.verify_clarify_http --session SESSION_UUID --approve-file approved.json --model-mode real
```

`answers.json` 为消息数组，例如 `[{"content":"用户实际回答"}]`；approve 格式为 `{session_id,brief_version,research_brief}`，必须人工审阅当前版本（包括 assumptions）后填写。`--model-mode` 是操作者声明，不从 /health 推断模型真实性。超时/503 不认为接受成功；再次操作前 GET 核对，输出trace保存请求键供诊断。暂仅开发匿名模式，正式认证扩展归T053。

新增 `tests/integration/test_verify_clarify_http.py` 3项：独立 uvicorn 后端进程+真实 TCP 客户端+隔离PG；分别在 ask、confirm、ready 后退出/重新启动进程，验证同一Session恢复；旧版/不同内容批准拒绝且零Run；独立CLI subprocess通过真实HTTP且不自动确认；非法回答字段在任何HTTP前拒绝。受控服务仅存在 `tests/support/mono_http_server.py`，强制显式 controlled 标记与 `dr4a_test_<uuid>` 库名，不是正式生产回退。

```bash
uv run pytest -q tests/integration/test_verify_clarify_http.py
uv run pytest -q tests/unit/test_mono_clarify.py tests/unit/test_mono_llm_bounds.py tests/integration/test_verify_clarify_http.py
```

首次因缺验证器模块而收集失败，再实现；TCP三项通过（7.14s）。后续带澄清反例定向25项通过（9.96s）。真实模型不是这3项里的受控模型，见下方单独记录。

## 真实模型过程中发现与修复的问题

1. 原默认SDK输出上限4096，实测首轮返回截断JSON，被503 model_output_invalid拒绝，未建Session。诊断调用再次看见截断文本；未取得那两次原始SDK stop_reason，不能把token上限推断伪称直接测量。canonical HTTP输出上限改为16384，SDK对stop_reason=max_tokens明确拒绝（即便文本碰巧是合法JSON），并保留严格解析。
2. 默认provider thinking可能消耗同一输出额度是上述问题的解释之一，不声称单因果证明。[DeepSeek兼容文档](https://api-docs.deepseek.com/guides/anthropic_api/)说明thinking支持但budget_tokens被忽略；[官方更新说明](https://api-docs.deepseek.com/zh-cn/news/news250528/)说明输出上限包含思考过程。未偷偷切换模型或关闭thinking，未改.env。
3. 增大后首轮ask成功，但一次后续仍重复旧问题/旧假设。进一步明确prompt：最新用户回答更新/覆盖旧草稿，JSON字段也是有效研究数据；不可信不等于忽略需求；missing_fields只表示未解缺口，field_reasons不是所有已填字段的提取解释。另一失败会话出现完整patch仍全部标缺；保持ask、零Run，没有绕过模型安全发现。
4. 假设按行稳定去重，防多轮重复追加同一披露；旧语义缺口不能因字段非空而强行清除。新增反例覆盖。
5. canonical HTTP关闭SDK内置重试，Clarify统一最多3次调用、其中至多1次Schema/截断修复；重试和修复合计不超过3。每尝试有界timeout，网络1/2s退避，请求租约可续。修复prompt含Schema位置/类型而非Pydantic原始异常/密钥。4项初始反例全部red后实现，又补provider截断占用单次修复的反例。未宣称全Run工具预算/结果缓存完成（T020）。

## 最终真实会话

独立uvicorn默认 `interface.main:app`，真实远程 `deepseek-flash`（环境配置alias，不等于已锁定provider不可变revision；模型锁定T056未完成）、真实隔离PG，默认papers/web。仅执行Clarify，未搜索、分析或生成报告。

- 测试库：`dr4a_test_ed47572329064081a5e10abd3de96550`。
- Session：`5e0be119-46aa-44c6-9ab9-2ce0c5356245`。
- Run：`78c8027a-17dd-47c1-8ab2-608d5200873d`。

初始 `POST /research {query:"我想研究入侵检测。"}` →201 ask，version1/round0、2问题；run_id/SSE URL均空。请求ID `9fc380fd-1066-4112-ae23-b9f41b1cd149`，键 `2feff0d8-4ae4-4a46-a6b8-32061efde2fe`。

后续 `POST /research/{id}/messages` 的 content 是下面对象的JSON字符串，brief_version=1：

```json
{
  "task_type": "evaluation_design",
  "decision_goal": "设计Transformer与CNN网络入侵检测论文评测可比性的审查协议，不开展新实验。",
  "research_object": "公开论文中的Transformer与CNN网络入侵检测模型及其评测协议。",
  "scope": "2021至2026年公开原文；限网络流量，排除主机日志。本次只是HTTP澄清功能的公开测试会话。",
  "comparison_scope": "按相同数据集版本、划分、防泄漏措施和指标定义分组；不预设排名。",
  "claims_to_verify": "检查相同协议下F1及误报率的报告，判断跨论文数值可否直接比较。",
  "evidence_requirements": "关键事实需原文页码、表格或段落定位；记录划分、单位、重复实验和统计报告，缺失标记Gap。",
  "conclusion_boundary": "仅提出证据审查协议，不声明模型更优、因果、生产适用性或完成实证实验。",
  "deliverable": "中文评测协议报告，固定0至5章节加参考文献，包含协议-指标-结论映射与局限。",
  "assumptions": "这是公开端到端功能测试，不是真实科研结论；无额外数据集或性能假设。"
}
```

→200 confirm，version2/round1，research_brief与上面十字段逐项相同。请求ID `5a841cb6-4874-488e-9885-39b4331562d6`，键 `c3ac000e-f0c3-4cff-82d2-c9646fcd9dff`。只读SQL读取该轮assessment：missing_fields=[]、questions=[]、field_reasons={}、assumptions=[]，patch完整；没有接受此前未解决的字段。确认前零Run。

测试操作者Codex审阅它与预设公开fixture完全相同后，显式输入 `APPROVE 5e0be119-46aa-44c6-9ab9-2ce0c5356245 2`，再经验证器核对最新GET并发送 `POST /confirm {accepted:true,brief_version:2}`。这是用户授权E2E内的测试操作者确认，**不冒充用户批准某项真实科研任务**，也不是验证工具默认自动接受模型假设。

→202 `{session_id,run_id,status:"ready",brief_version:2,sse_url:"/research/5e0be119-46aa-44c6-9ab9-2ce0c5356245/events"}`。请求ID `5b50d7aa-7c86-46ba-b347-908d42f33f35`，键 `6b98f890-9b21-4975-befc-0af90bdfee38`。

退出并重启后端进程，GET请求ID `6d7e86fc-c2ec-436b-8859-d4d8fe6f6f85` →200：同一session/run、statusready、phaseplan、revision3、version2、checkpoint_seq1、完整冻结Brief不变。没有执行/重新付费跑Pipeline。

### 只读SQL与清理

确认/重启后用 `conn.transaction(readonly=True)` 查询：sessions_count=1、research_runs_count=1、phase_snapshots_count=1、frozen_brief_count=1、checkpoint_seq=1、attempt_count=0；confirmed_by=`00000000-0000-4000-8000-000000000001`，content_hash=`7372bbb5ba4e6ab68a6f0a9b25d714823eca2d8c120e589c0811a3e353cba9ce`。

后端进程/pool均退出，准确删除这个生成的测试库。前述失败尝试的唯一测试库也准确清理；不删除用户库/表，不迁移用户实际库，不打印.env密钥/DSN。以上ID为本次证据，测试库已删除，不能当作用户可继续访问的会话。

## 验收边界

提交前最终 `uv run pytest -q`：**299 passed in 42.05s**；修改路径ruff与git diff --check通过。该总数包含受控模型与隔离PG/TCP测试，不把299项都称为真实模型测试；真实模型结果为上述单独手工活HTTP会话。

T013公开来源真实模型澄清、明确确认、重启恢复和PG冻结验证已通过；T008确定性反例亦已通过。三task枚举、上限、退回/并发/故障等由定向测试证明，不声称单条真实会话覆盖全部场景。T011的KB授权/隐私仍依赖T041/T050而保持未完成。US2、真实研究/报告、Web未完成，goal继续，不宣称全后端或所有M1任务已完成。
