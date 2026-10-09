# 调试记录：第一次真实跑通研究工作流（2026-10-09）

> case1（idea_exploration）从"从未有真实运行发布过报告"，到产出第一份真实报告，一共踩了约 27 个坑。本文按出现顺序记录，并总结其中的共性规律。
> 相关提交：`0f2361d`…`4993656`（分支 `fix/research-api-contract-alignment`）。后续任务见 `backend/lab/NEXT.md`。

## 0. 结论先行

- 27 个坑里，**只有 2 个是"模型能力不够"**（检索词写法、写作 hedge 过多）。**其余全是系统自身的问题**：硬门、拍脑袋定的上限、被压扁的错误、各层规则互相矛盾、上下文膨胀、provider 特性没摸清。
- 之前在 goal 模式下屡次失败的根本原因有两个：一是 harness 先于工作流建设，二是从来没有一个能看到真实异常和完整 prompt/response 的运行入口。lab runner（`backend/lab/run.py`）补上的就是后者。它上线后，平均每个坑从发现到定位只需几分钟。
- 最终结果：
  - 运行：32 分钟，62 次 LLM 调用，约 1.6M tokens，经过 3 轮返工。
  - 数据：15 个来源，142 条原文证据。
  - 报告：结论为 `needs_more_work`，约 54k 字。

## 1. 共性规律（比单个坑更重要）

| 规律 | 典型表现 | 应对原则 |
|---|---|---|
| **全有或全无的硬门** | 一个坏条目导致整章、整个来源乃至整次 Run 失败；只给一次修复机会 | 结构错误才失败。语义违规由代码丢弃或降级，并写入 trace，后面交给 Critic 和返工处理 |
| **上限靠拍脑袋** | 预算 120k tokens、单次 LLM 超时 60s、`max_tokens` 16k、writer 上下文 192KB | 先用宽松的 lab 限额跑出真实成本，再定默认值 |
| **错误被压扁** | 异常一律变成 `dependency_unavailable` / `invalid_state` / `model_output_invalid`，真实原因丢失 | 调试入口必须保留原始异常栈和原始 response |
| **各层规则不一致** | Writer 允许的写法在交付门被拒；覆盖规则与 planner 的写法对不上 | 同一条规则只实现一处，或者让上游镜像下游的检查 |
| **上下文无界膨胀** | 302 条 claim、200KB 的 coverage 对象被整个塞进 prompt | 按预算挑选 + 摘要化；被省略的内容计数后告诉模型 |
| **provider 特性** | DeepSeek 的 thinking 与答案共用 `max_tokens`；SDK 非流式调用有输出上限 | 用真实 API 做小探针确认行为，不要凭文档推测 |

## 2. 按时间顺序的坑

每条按"现象 → 定位方式 → 根因 → 修复"的顺序记录。

### 阶段 A：连不上、搜不到

**A1. LLM 报 `dependency_unavailable`，发出请求后 4 秒就失败**
- 定位：lab 把被吞掉的原始异常记进 trace，看到的是 `APIConnectionError`，底层是 TLS `EndOfStream`；这时用 curl 访问 DeepSeek 是通的。
- 根因：Claude Code 给子进程注入了 `ANTHROPIC_BASE_URL` 和 `ANTHROPIC_AUTH_TOKEN`，它们的优先级高于 `backend/.env`。结果 DeepSeek 的 key 被发到了 Claude Code 的代理（第一次返回 401，之后 TLS 断开）。**任何从 coding agent 里启动的真实运行都会中招**，之前那些"LLM 超时"很可能也有这个原因。
- 修复：lab 检测到 `CLAUDECODE` 时丢弃这两个变量；其他入口用 `env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN` 启动。

**A2. 错误信息无法定位**
- `structured.py` 把所有外来异常统一转成 `dependency_unavailable`。
- `cli phase` 把 merge 阶段的 `ValueError` 统一转成 "Phase result failed canonical state merge validation"。
- `report_serializer` 把序列化失败统一转成 "Reviewed draft cannot pass report delivery checks"。
- 修复：lab 在工具层包一层，把原始异常写进 trace 并输出 `error.txt`。生产代码出于脱敏考虑保留了压扁的做法，但调试时必须绕开它（A26 就因此不得不临时加 traceback 打印）。

**A3. research 第一个查询 1ms 就报 `all_search_sources_failed`**
- 根因：默认的 web 检索 provider 是 `search_router`（本地网关 127.0.0.1:8080），而这个网关没有启动。
- 修复：先切到 Bocha，后来启动网关。

**A4. lab 里改了检索 provider，却不生效**
- 根因：`DebugTools` 内部会自己再调用一次 `Settings.load()`，lab 传进去的覆盖值被忽略。
- 修复：lab 改为通过环境变量传值。教训是：配置有多个加载点时，测试时的覆盖很容易落空。

**A5. Bocha 检索质量极差**
- 现象："CERT" 匹配到了中文的 CNITSEC 证书公告、Telnetd 漏洞通告、CSDN；32 个来源只抽出 5 条证据。
- 结论：弱检索撑不起学术研究，前提条件是检索源的质量。
- 修复：web 检索改用 search-router（Serper/Tavily），同时加上 paper 源。

**A6. 只有 research 阶段就用掉 42 次调用、145k tokens**
- 这已经超过了设计文档给整个 Run 的默认预算（120k tokens），说明预算是拍出来的。
- 修复：lab 用宽松限额（5M tokens / 400 次调用）跑出真实成本，见 §3。

### 阶段 B：Writer 与抽取的硬门

**B1. write 失败："Chapter citation has no claim/evidence relation"**
- 这与历史上第 5 次失败一致。
- 根因：`materialize_chapter` 只要发现一句引用不合规，就把整章作废，并且只给一次修复机会，修复失败即 fatal。
- 修复：新增 `repair_citation`：
  - 丢掉未知或无关的 ID；
  - 没有证据支撑的 factual 句降为 hypothesis；
  - 行引用数量对不上时自动补齐或截断；
  - 每处修复都记一条 `writer_repair` 事件。

**B2. 抽取失败："Observation value normalizes or invents an original number"，修复后反而更糟**
- 现象：第二次尝试出现了 6 个 "Invalid decimal string" 错误。
- 根因有两个：
  1. 一个坏数字就让整个来源的抽取作废；
  2. 修复 prompt 里只带了错误的类型名，没有带错误消息，模型不知道自己违反了哪条规则。
- 修复：
  - 每条 quote、claim、relation、observation 单独校验，不合格的丢弃并记录原因；
  - 数值一律由代码从原文中提取，模型给出的 `value` 只作参考；
  - 修复 prompt 带上完整的错误消息；
  - 只有"全部丢光"时才触发修复；
  - 单个来源抽取失败时记为缺口，不再拖垮整次 Run。

### 阶段 C：检索质量

**C1. arXiv 不可用**
- 现象：
  - API 按关键词机械匹配，`SEI` 召回的是电化学的 SEI 膜论文；
  - 用 AND 连接的长查询返回 0 条；
  - 本机访问 `export.arxiv.org` 约一半请求连接失败。
- 修复：新增 OpenAlex（只返回带开放获取 PDF 的论文；查询太长时从末尾逐个删词重试），设为默认 paper 源，arXiv 仍可选。

**C2. plan 把中文研究问题当成检索词**
- 现象：查询 "本任务支持的具体决策和交付物粒度是什么？" 搜回的是腾讯云页面。
- 根因：章节没有给出 anchors 时，系统会退回用 `sub_questions` 当检索词；而 prompt 也没要求 anchors 写成什么形式。
- 修复：plan prompt 要求每章给出 2–6 个英文、关键词密集、带具体对象的 anchors，并附上 gold trace 风格的示例；只复述 brief 的章节不检索。

**C3. 论文候选永远选不上**
- 根因：候选按"前 4 个"截取，而结果列表里 web 排在前面。
- 修复：按 provider 轮流选取（`zip_longest`）。

**C4. 论文 PDF 全部抓取失败**
- 根因：只有 HTML parser 可用；PDF parser 需要 MinerU，而 MinerU 没有配置。
- 修复：新增 `dr4a-light-v1`，用已安装的 pypdfium2 读取文本层，并保留页码。实现中还踩了两个小坑：
  - pdfium 的换行符是 `\r\n`，把 `\r` 替换成 `\n` 后每行都被当作段落，9 页 PDF 切出了 659 个块；修正后是 64 个。
  - `U+FFFE` 是 pdfium 的软连字符标记，会出现 "em￾ployers" 这样的文本，需要删除。

**C5. "Original extraction conflicts with a stable fact"**
- 根因：两个查询从同一原文位置抽到了同一条证据，但 `evidence_type` 不同，被当成致命冲突。
- 修复：已提交的事实不可变，保留第一次读到的版本，差异写入 `fact_reread_differs` 事件。

**C6. OpenAlex 偶发报 `search_response_invalid`，且所有 provider 都失败时整个 Run 中止**
- 根因：
  1. 10 条结果里只要有 1 条的 `pdf_url` 不合法，整次检索就被判为无效；
  2. 网关的 Tavily 熔断，恰好与 OpenAlex 失败同时发生。
- 修复：单条坏记录跳过；某个查询所有源都失败时记为缺口，不再中止 Run。

### 阶段 D：thinking 与输出上限

**D1. 单个 research 单元耗时 234 秒、输出 58k tokens；还有一次输出的正文是 0 字符**
- 定位：在 trace 里按调用统计 `output_tokens` 和 `stop_reason`，看到 `max_tokens` 被打满。
- 根因：DeepSeek 的 thinking 与答案共用 `max_tokens`，机械性的抽取任务也在长时间思考。
- 修复：新增 `LLM_NO_THINKING_PHASES`（research、write）。改后同一单元 31 秒、8k 输出，research 阶段整体快了 3–8 倍。

**D2. review 被截断**
- 现象：review 是最需要 thinking 的阶段，不能关掉；`max_tokens` 调到超过 21k 时，SDK 报 "Streaming is required"。
- 修复：LLM 调用改为流式（`messages.stream` + `get_final_message`），上限提到 48k。
- 连带问题：集成测试的 mock 模型服务返回普通 JSON，流式 SDK 读不了，导致 6 个用例报 `tool_call_uncertain`。修复方式是新增 `http_message()`，把 Message 编码成 SSE。

### 阶段 E：上下文膨胀

**E1. write 报 "Chapter context exceeds its bound"**
- 现象：302 条 claim，相关章节的上下文有 270KB。
- 修复：`select_chapter_facts` 按查证状态和原文支持广度排序，在字节预算内选取；被省略的 claim 计数后写进 `omitted_claims` 告诉 Writer。

**E2. 选了事实之后仍然超限**
- 定位：逐个字段测量上下文大小，发现 **coverage 对象有 200KB**：每条 claim × 每个 spec 各一行 gap，外加一份重复的 `unresolved_items`。
- 修复：只给模型按 ClaimSpec 汇总后的摘要。教训：**不要猜哪部分大，要逐个字段量。**

**E3. review 超出 384KB 上限**
- 修复：Critic 只看被草稿引用的事实，加上 coverage 摘要。

**E4. 返工轮的 write 又超限**
- 根因：
  1. 预算没有算上修订上下文（上一版草稿和 feedback）；
  2. 预算按 `str` 长度计，而 prompt 上限按 UTF-8 字节计，中文场景下两者相差约 3 倍。
- 修复：先扣掉修订上下文再分配预算，统一按字节计。

### 阶段 F：覆盖与审阅语义

**F1. 302 条 claim 中有 275 条 insufficient，没有一条 supported**
- 根因：planner 把 `required_conditions` 写成了句子（例如"以 r4.2、r6.2 为主…"），代码却拿它当 claim.conditions 字典的键去匹配，永远不可能满足。
- 修复：这些散文形式的条件只作为写作和审阅的边界，不参与支持判定；只有来源等级仍然作为门槛，等级不足的支持记为 limited。
- 修复后的分布：56 supported、188 limited、58 insufficient。

**F2. write 输出了 JSON 之外的 Markdown 正文，或多出 `kind_note` 字段**
- 修复：
  - 能从正文中解出 JSON 对象时取出它；
  - 内容类输出（write、research）丢弃多余字段；
  - 控制类输出（clarify、plan）仍然拒绝多余字段，防止伪造 status 或 phase。

**F3. Critic 违规设置 `fillable`，被硬拒**
- 现象：Critic 给非 missing_source 类的问题设了 fillable=true，或者漏掉了这个字段。
- 修复：由代码统一归一。fillable 只用于决定 missing_source 的返工路由，这类派生字段不该交给模型保证。

**F4. 达到返工上限后报 "Terminal contraction still has unsafe unresolved review issues"**
- 根因：规则写成"只要该章节存在任何 factual 句就不安全"，没有看问题实际指向哪一句。
- 修复：按问题指向的那句话判断。问题都落在非 factual 句上时，可以以 needs_more_work 交付并列入风险；问题落在 factual 句上时仍禁止交付。

**F5. build_report 报 "Cited evidence does not satisfy planned source tiers"**
- 根因：Writer 降级时没有检查来源等级，而交付门检查了，两层规则不一致。
- 修复：Writer 端镜像交付门的检查（`_tier_backed`），已保存的草稿可以用 `downgrade_unbacked_facts` 重新处理。

**F6. 报告有 1377 条风险，第 5 章长达 214k 字**
- 根因：风险清单是每个 gap 行、每条非 supported 的 claim 各生成一条。
- 修复：按 ClaimSpec 聚合，只列被引用的 claim，降到 88 条。
- 连带问题：聚合后排序时，有些 `claim_spec_id` 是 None，抛出 `TypeError`，又被序列化层压扁成 `invalid_state`。

**F7. 返工的 re_research 没有发起任何检索**
- 现象：这一轮 0 次调用，只重算了 coverage。
- 状态：**未修复**，已列入 NEXT.md 第 1 项。按设计（dataflow §3.3），这一轮应该针对 gap 定向补查。

## 3. 真实成本（case1，供定预算参考）

| 阶段 | LLM 调用 | tokens | 时间 | 备注 |
|---|---|---|---|---|
| plan | 1 | ~14k | ~50s | 开启 thinking |
| research（17 个单元） | ~43 | ~450k | ~20min | 先开 thinking、后关 thinking 的混合数据；全程关闭时约 10min |
| write（5 章） | 5 | ~250k | ~96s | 关闭 thinking，prompt 约 155–170KB |
| review | 1–2 | ~100–130k | 60–130s | 开启 thinking，输出约 20k |
| 合计（含 3 轮返工） | 62 | ~1.6M | ~32min | — |

设计文档里的默认值是每 Run 60 次调用、120k tokens、单次调用 60s，比实际需要低了一个数量级。

## 4. 工具与方法上的教训

- **先建可观测的运行入口，再修 bug。** lab runner 的四个要素：
  - 每个单元都保存 state；
  - 完整记录 prompt 和 response；
  - 保留原始异常栈；
  - 可以从保存的 state 续跑。
- **续跑比重跑重要。** 一次完整运行约 1.5M tokens。改 writer 时从 write 阶段续跑；改序列化时直接对保存的 state 调用 `build_report`，不花 LLM 成本。
- **改规则后需要把派生数据重算一遍。** 例如 `--recompute-coverage`、`downgrade_unbacked_facts`、prompt 版本变化后自动刷新。
- **测试里的硬编码数字是坑**：provider 调用次数、"arXiv 休眠"这类断言会在行为合理变化时失败。修改这类测试时要在用例里写明新语义，不能删掉覆盖。
- **当心格式化工具**：`ruff format` 会顺手改动无关文件，提交前要检查 diff 范围。
- **在 coding agent 里跑后端**，务必检查继承来的环境变量（见 A1）。
