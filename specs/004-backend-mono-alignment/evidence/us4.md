# US4：五阶段调试链与真实运行记录

日期：2026-10-08。代码基点 `29e10f5`，下载复验单独提交为 `80b562e`；本节对应其后的工作流改动。**不是 T039 完成或研究质量验收。** 三任务真实 HTTP 报告、数值分析模板/沙箱组合及 KB 路径仍缺。

## 已接通的能力与边界

- `application/phase_workers.py::public_workers` 注册五个 canonical worker，开发 HTTP 执行器与 real CLI 复用；不调用旧 Writer/Critic。旧函数隔离在 `legacy_writer.py`、`legacy_critic.py`，仅供尚未迁移的明确 legacy 调用与回归。默认 fake run 仍待 T021 迁移，不能宣称已删除全部旧链。
- Writer 使用章节关联的 Claim/Evidence/Source、关系、ComparisonSet/Metric、Artifact、coverage、旧稿和反馈；模型返回段落与三任务结构，代码生成 Statement/Binding/版本及正文投影。未改章节的绑定复制到新全局版本。材料超过硬上限明确失败，不静默裁掉事实。
- Critic 检查登记正文/引用，再审阅实际同版稿、全部事实与关系、比较条件和历史问题；历史问题须逐项复核。未知目标和空事实绑定拒绝；coverage 不足不得因模型说 approved 升级。
- Prompt 描述工作性质与判断依据，Writer/Critic/原文抽取提供内容型 few-shot；输出交给严格 Pydantic Schema。冻结版本包含 Prompt、示例与输出 Schema 的 hash；上下文组装策略也有版本标识。保留用户已修改的 Clarify Prompt。
- Analyze 无需求时记录 skip；有需求从原始 Observation 建指标与 ComparisonSet，缺关键条件（两个未知也不相等）或跨数据/协议不能 compatible。目前**没有执行数值模板**，即使条件兼容也写 Gap，不产生伪造 Artifact。T029–T032 未据此完成。
- Budget 在研究单元中途耗尽时，Coordinator 提交完整收缩 Checkpoint：保留已提交事实、不可完成工作写 Gap，不把中断的 query 写入 manifest 或发 query_completed。之后仅一次 write/review，仍不能安全发布则 failed。取消/失租与预算账本的原有门仍有效。
- 单次模型 reservation 的绝对上限使用 Run.tokens；非终末扩展与收尾预留的分离由 PG 预算锁执行。不能在上层再扣一次预留，以致收尾阶段无法使用它。输入 UTF-8 字节 + 输出额度仅是保守预留，实际消耗仍使用供应商 usage，不把字节数伪称 tokens。

## 确定性模型 + 真实存储/TCP 验证

```bash
# backend；需现有 PG/MinIO 及可调用的 Node
uv run --no-sync pytest -q --tb=short \
  tests/integration/test_mono_cli_run.py tests/integration/test_mono_run_driver.py
uv run --no-sync pytest -q --tb=short \
  tests/unit/test_pipeline_workers.py tests/unit/test_phase_contracts.py \
  tests/contract/test_mono_report.py
uv run --no-sync pytest -q --tb=short tests/unit/test_verify_cli_workflow.py
uv run --no-sync pytest -q --tb=short \
  tests/integration/test_tui_live_http.py tests/integration/test_tui_terminal.py
# tui
npm test
npm run typecheck
```

- real CLI 组合与 Driver 目标集 **37 passed in 33.87s**：真实 PG/MinIO、SDK HTTP 传输，外部模型/搜索明确受控；实际默认五 worker 完成固定报告。中途 Fetch 预算耗尽反例：7 次模型调用、1 次 Search/1 次 Fetch，保留 query_started、无 query_completed、无 Research manifest；报告 needs_more_work，零原文不填假引用。不是公网研究验收。
- Writer/阶段合并/报告目标集 **94 passed in 1.70s**；补入关系/比较上下文后，Writer 与 CLI 审计单测目标集 **22 passed in 1.35s**。包含三任务 payload、事实无引用拒绝、未改章节 Binding 保留、未知审核目标、有界修复、零值与未知/跨协议条件、材料超限在调用前拒绝。
- CLI 审计反例先因缺 `audit_checkpoint` 失败，实现后 **7 passed in 0.14s**：Run/Session/seq/phase 身份、原冻结 Brief、同版审核、同报告与确定性发布内容必须一致；篡改正文即使返回值与保存值一致也不能通过。
- TUI 自身 **22 tests passed**，typecheck 通过。canonical worker 的活 TCP TUI/PG/MinIO 测试已加入，外部模型/搜索受控，最终回归结果追加下节；不能用这些代替真实模型或终端样式评估。

## 三次真实供应商失败（原记录保留）

公开输入：[方法辨析 Brief](us4-web-brief.json)。实际 `.env` 配置模型为 `deepseek-flash`（API 不提供可验证的精确权重 revision，快照保留 unconfigured，不伪称固定权重），Bocha web、原 RestrictedDownloader、HTML Parser、PG `dr4a_debug` 和真实 MinIO。创建全新调试 Session/Run，不自动恢复不确定付费调用、不修改 `.env` 或用户成果。

1. [60 秒超时](us4-web-real-20261008.json)：Session `09134b85-89c5-4704-b18b-6488f960cf8b`，Run `4583824e-5744-40ad-b175-5cdff9004ad6`；plan 无提交，tool_call_uncertain。不是空计划成功。
2. [延长单次超时后、DNS 修复前](us4-web-real-20261008-retry.json)：Plan 成功；网页 Fake-IP 拒绝使下载预算耗尽，零事实，无报告。记录为历史失败，不因代理恢复改写为通过。
3. [redir-host 后，默认总预算](us4-web-real-20261008-redir-host.json)：Session `8cdb8477-4fad-4a66-8272-d48ee35f2d33`，Run `756d2f15-0059-402f-9e95-2f9e5eb3fa73`。已提交 9 Source、16 Evidence、16 Claim；0 Observation/Artifact。seq=10 从 research 收缩到 write，已用 11 次模型调用/5 Search/17 Fetch/83,019 tokens。仍因写作调用的保守输入/输出预留不足失败，无报告。该结果证明 DNS 障碍解除及真实事实提交，不证明整个 workflow 跑通。

单次 LLM 超时在后两次显式设为 180 秒；项目默认 60 秒未改。禁止把模型超时、普通网络/HTTP 波动或硬预算失败解决为 SSRF/TLS 例外。

## 大收尾预留的真实失败与Schema诊断

新建 Session `079155d8-0518-492b-b9e1-eb40c3bad0a2`、Run `09400e61-fdfd-4eab-84e5-b0746b6bae15`，显式配置 `RUN_TOKENS=300000`、`RUN_TERMINAL_RESERVED_TOKENS=200000`、`LLM_TIMEOUT_S=180`，其余沿用 HTML/web 调试配置。[失败记录](us4-web-real-20261008-terminal-budget.json)保留：seq=4进入write，Research未完成的单元未提交，因此0事实；之后未发布报告，外层错误为dependency_unavailable。

只读PG工具账本与MinIO已存结果显示：10次模型调用全部有供应商usage/结果对象；首个Plan响应max_tokens（16384输出tokens）后有界修复成功；3次原文抽取完成但query未提交，5次Writer调用中最后普通章节错误生成6项row_citations，修复一次仍6项。这不是网络故障，而是章节语义校验失败。改为普通章 `PlainChapter`（payload只能null、row_citations maxItems=0），核心章按冻结TaskType选择非空结构；模型看到实际局部Schema，不追加禁令Prompt。三个Schema反例先失败，修正后通过。

TaskRunner此前把所有AdapterError转为dependency_unavailable，掩盖model_output_invalid；现在只保留已知LLM shape/usage错误码，其他未知供应商code/message仍脱敏。PG反例先2失败/1通过，修正后和CLI/TUI/Writer目标集 **57 passed in 54.06s**。没有改写旧Run的已存失败。

本实验**不是默认 120000/12000 预算验收**；原默认值和先前失败记录保留。分章Schema的下一次真实复验使用新Session `cb5fefc5-c9ad-46f8-8573-9e45588bc7fa`、Run `0844c2a8-1b7e-41af-b121-533c9687f1c4`，显式500000总tokens/200000收尾预留。[终结记录](us4-web-real-20261008-chapter-schema.json)：Research提交17 Source、9 Evidence、11 Claim，0 Observation/Artifact，seq=12收缩到Write；19:58:58失败为model_output_invalid，未发布报告。

只读PG账本与MinIO缓存重放显示：第一个章节响应通过局部校验；第二章节及其一次修复均被 `Unverified hypothesis cannot become factual prose` 拒绝。普通章的payload为null、row_citations为空，因此不是先前普通章误生成核心行的问题；这次是尚未满足事实资格的Claim被写作响应当成事实。没有放宽事实门、改写Claim状态、修改已冻结Run或继续重放付费请求。缓存重放仅输出字段位置、错误类型与通用校验原因，不输出凭据或原文。原文下载恢复不等于真实报告闭环完成。

## T017/T033与回归完成复核

T017的阶段执行、严格切片/白名单合并、不可变unit_manifest与完整seq、Machine阶段转换、先事务后投影已接canonical五workers。真实CLI子进程/SDK传输/PG/MinIO和TypeScript客户端→活TCP后端的全阶段报告由受控外部结果验证；不证明生产默认能力或真实报告质量。

T033反例完成于 `test_pipeline_workers.py`、`test_phase_contracts.py`、`test_mono_machine.py` 与 `test_mono_report.py`：实际Writer未改章节绑定复制、Critic审阅真实文本、section_3表行事实无绑定在模型前拒绝、普通事实空引用拒绝、critical/major各八种路由、返工上限不直接升级。结合CLI审计反例最终 **130 passed in 2.12s**，Ruff通过。因此T017/T033可按本任务范围勾选，不由此勾T034/T035/T039。

全量回归（不含未提交的独立Milvus WIP实测）曾因缺Node PATH的6项客户端启动失败和1项旧预算测试路由假设失败，均保留失败结果而非跳过：补齐真实Node运行环境，将检索预算测试限定在其真正Tool边界（中途收缩另由实际默认worker CLI覆盖），得到 **1144 passed in 242.36s**。最后分章Schema/错误分类修正及反例加入后的最终全量 **1156 passed in 243.75s**：`pytest -q --tb=short --ignore=tests/integration/test_mono_milvus.py`。Node使用本机v24.13.1，测试资源由fixture唯一隔离和精确清理；Milvus实测属于独立未提交WIP，不算本批覆盖。Ruff与diff检查通过。没有以fake报告认定真实E2E。

## 未完成

T039 的三任务真实 HTTP 报告、cases 粒度与引用人工抽查；默认预算下的收尾容量实测及提前停止政策；完整有限追溯/补查、可执行五种分析模板/真实沙箱回链；生产默认能力装配与 readiness；fake CLI 迁移。T026/T028/T034/T035/T039 不因这一批有限验证统一勾选。

## 2026-10-08：借鉴 Open Deep Research 的 Prompt 结构试验

用户提供的 [LangChain prompts.py](https://github.com/langchain-ai/open_deep_research/blob/1b7d2e80db9faa586165c60e09096dbbfd483a64/src/open_deep_research/prompts.py) 已固定到提交 `1b7d2e80db9faa586165c60e09096dbbfd483a64`，同时核验 deep_researcher.py 的调用路径。借鉴任务、材料、工作方法、质量要求与案例的分区表达，局部重写 canonical Writer/Critic；上游没有对应 DR4A 的 Critic，其审阅内容是本地编写。未复制上游自由报告结构、URL编号、think_tool、模型控预算或自由Supervisor。Clarify、Plan、Research抽取、Schema、事实门和共享契约不变。

Writer示例分别展示受支持的机制与未知性能、原文存在但主张insufficient、待执行评测方案。Critic解释为什么换成hypothesis标记却仍声称效果已证实不能解决overclaim。通过实际局部Schema验证的新增反例：insufficient Claim即使有原文关系也不能写成factual；条件性hypothesis可保留其来源身份，原始事实状态不变。

真实试验使用上一轮失败Checkpoint的**本地副本**，只把Prompt版本换为新hash、Write目标限定到section_2；其余Brief/事实不改。运行 `uv run --no-sync python -m cli phase write --state LOCAL_COPY --real --json`，不是恢复原PG Run，也不创建Session或发布Report。输入仅公开开发Brief/公开网页事实，无私有KB或凭据；不触发Search/Fetch。[结果记录](us4-writer-prompt-structure-20261008.json)：2次模型调用，22,533输入/21,089输出tokens，退出3、model_output_invalid，零state_delta。CLI未保存原始模型响应，因此本次无法区分具体引用校验、其他Schema或输出问题；不把旧Run的具体失败原因直接套到本次。

**仍失败，不认定改写有效或真实E2E通过。** 单次新样本与历史失败不是受控A/B或泛化测试，Critic本轮没有真实供应商复验；未追加付费重试、升级Claim状态或放宽Schema。首次新增反例因fixture残留其他章节的Claim引用导致2失败/123通过，修正fixture归属后目标集 **125 passed in 1.93s**；CLI phase/run、活TCP TUI和Clarify确认回归 **31 passed in 62.62s**（真实PG/MinIO、受控外部模型）。Ruff/format/diff检查通过；本批未重跑上一批1156项全量，不把该旧数字冒充当前全量。
