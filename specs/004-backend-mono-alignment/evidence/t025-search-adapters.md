# T025：搜索候选契约与受控并发

日期：2026-10-06。此证据只覆盖候选检索，不覆盖正文、Evidence、真实论文取证或完整 Pipeline。

## 实现范围

- `domain/research/search.py` 定义严格 SearchResult、SearchOutcome、SearchBatch；`domain/ports.py` 复用同一候选类型。候选的摘要不是原文 Evidence，也没有 quote/location/content hash 字段。
- arXiv 保留作者、发布时间、明确返回的版本与 PDF 候选链接；作者预印本标为 primary，不自动声称 peer_reviewed。未返回版本时保持空字符串，不假造版本。
- Bocha 保留 URL、标题、摘要、发布方与时间，来源层级默认 unknown。搜索平台不替目标页面背书。兼容旧 Scout 的来源投影也去除了 paper → peer_reviewed 的猜测；旧 Scout 的摘要取证仍待T027替换。
- Provider Adapter 每次只发一个物理 HTTP 请求，无隐式重试，不跟随重定向；HTTP、网络、超时、200内业务错误、畸形响应、超大响应均为脱敏 AdapterError，不返回伪空数组。
- 成功且合法的空 feed / `webPages.value=[]` 才是 empty。错误 Atom feed 不是论文。
- `search_batch()` 按来源并行，默认共享实例限流4，支持组合根注入跨请求 semaphore；每源单次20秒总 deadline、最多2次且仅 retryable 重试。arXiv 的共享 Adapter 额外限制请求起点间隔3秒。
- 返回按注册顺序排列的请求局部 outcome，无共享 gap 副作用；部分失败保留其他候选，全部失败明确 `all_failed=true`。旧 `search()` 兼容列表接口，全源失败抛 `all_search_sources_failed`，不返回空数组；旧 take_gaps 仅供迁移前 Orchestrator 使用。
- categories 显式过滤 papers/web，不能通过外部搜索读取 knowledge_base；自定义来源参与过滤时必须声明类别。
- 每个物理 attempt 均可通过调用者 `invoke` 钩子计量/缓存；钩子的 lease/budget/控制错误向上抛出并取消其他任务。**这只是接入点，不表示正式 PhaseTools 已接入搜索账本**，T020/T027仍需真实PG/MinIO证明。探针是显式无Run的单次外部请求，不冒充Run计费记录。
- HTTP客户端复用、连接数/响应大小限制、明确 owned/injected 客户端关闭规则；Composition root 传递同一 timeout 并关闭 CompositeSearch。

## 可执行验证

从 backend 运行：

```bash
uv run pytest -q tests/contract/test_search_runtime.py tests/contract/test_search.py tests/integration/test_slice_retrieval.py tests/unit/test_scout.py
uv run python -m scripts.verify_search_adapters --json
```

前者 **58 passed in 0.37s**：受控HTTP测试覆盖结构、空结果/错误、一次物理请求、401/429/503/302、不泄露凭据/正文、2MiB上限、缺key零请求、并发实际重叠/全局峰值、来源隔离、重试每次经过调用钩子、控制错误/取消不被吞、起点间隔、部分及全源失败。它不是外部服务可达性证明。

真实探针用 `Settings.load()` 读取现有 backend/.env，不打印/修改 key，未连接PG或执行LLM。默认公开查询为 `Attention Is All You Need transformer`，默认禁用重试：

```json
{
  "scope": "public_search_candidates_only",
  "elapsed_seconds": 20.004,
  "all_failed": false,
  "sources": [
    {"source": "arxiv", "status": "failed", "attempts": 1, "count": 0,
     "failure": {"code": "search_timeout", "retryable": true}},
    {"source": "bocha", "status": "ok", "attempts": 1, "count": 9, "failure": null}
  ]
}
```

实际网络暴露 arXiv 不可达/未在20秒内返回，**没有宣称其联网验收通过**。Bocha候选第一项为网页而非论文，不能推定9候选均相关，更不能推定有可下载原文或有效观察。筛选/获取/摘录证明属于T026–T028。

全量回归在独立验证PG `dr4a-verify-pg-20261006-1041`、唯一测试库/MinIO桶运行：**656 passed in 80.71s**。未修复/清理因PSSD故障损坏的原PG，未改 .env 或用户数据。Ruff与 `git diff --check` 通过。

## 外部响应格式依据

- [arXiv API manual](https://info.arxiv.org/help/api/user-manual.html)：Atom候选元数据、版本与200内错误feed。
- [Bocha官方MCP源码](https://github.com/BochaAI/bocha-search-mcp/blob/master/src/bocha_search_mcp/server.py)：web-search端点、query/count/freshness/summary与webPages字段；业务code和候选形状同时由本次真实响应验证。

用户提供的 industry_information_assistant 仅作只读控制流参考，没有复制、执行或修改其代码/凭据。其章节并发组织有参考价值，但“原型整体跑通”不替代本项目的原文证据及故障契约验收。

## 正式Run逐来源账本绑定（T020/T027部分）

`application/search_tools.py` 新增SearchBinding/SearchProvider与窄ResearchSearchPort；PhaseTools和RunDriver显式注入绑定，不调用旧Container、不自动启用fake。Worker只能在当前research query单元请求该单元的原定query，不能选provider/category、改预算、或覆盖replay权限。来源类别来自冻结SourcePolicy；private_only和纯KB scope在外部请求前拒绝。

每个provider物理操作经ToolCallService完成PG预算预留、attempt记录、MinIO不可变结果、结果提交；不同provider分别记账，重试保持相同语义call key且新增physical attempt。搜索候选类型必须与绑定provider类别一致，真实空结果缓存为empty，不写Evidence。

provider超时会留下保守收费的uncertain记录；只有当前调用已收到retryable provider失败后，第二次只读重试获准replay。新调用首次遇到既有uncertain仍必须显式coordinator授权。缓存损坏不重新请求provider、不转换成来源降级。并发账本错误保持AppError/AdapterError类型；完整性/租约错误不能被同时发生的预算耗尽掩盖。

验证：

```bash
uv run pytest -q --tb=short tests/integration/test_mono_search_tools.py
```

**6 passed in 5.31s**：真正Driver五阶段受控执行、真实PG/MinIO隔离资源中证明每来源缓存复用、超时后新attempt且账本消费一致、query/phase权限拒绝、缓存损坏不重查、耗尽预算在provider I/O前拒绝、既有uncertain不自动重发。provider返回/业务worker是明确受控fixture，不是公网论文或真实研究报告验收；预算耗尽fixture先通过真实ToolCallService/PG/MinIO耗尽搜索额度，再验证新的provider请求为零。

初始化的定向组合89项通过；后续新增uncertain反例单列上述6项。Ruff/format/diff检查通过。当前还缺Fetch预算/候选授权、正式research worker、KB/analysis工具和CLI默认组合；T020/T027仍未完成。不得以这些生命周期测试宣称关键技术Evidence已有原文。

最终版本全量 **831 passed in 139.19s**；隔离PG/MinIO测试资源清理完成，用户原库/volumes及 `.env` 未变更。提交不包含用户的 `docs/implementation/`。

本轮只读系统DNS复查（2026-10-06）：arxiv.org返回 `151.101.3.42`、`151.101.67.42`、`151.101.131.42`、`151.101.195.42`，`ipaddress.is_global`均true；旧Fake-IP记录不再是当前阻塞。此复查不证明arXiv搜索API、PDF Parser或完整research已经联网验收。

## 2026-10-08：执行配置暂不注册 paper search

用户调整优先级为 TUI 真实工作流。`PublicResearchExecution`（HTTP 与 real CLI 共用）、独立 CLI phase 工具和旧组合根均只构造 Bocha；不导入/构造/注册 arXiv。arXiv Adapter、明确编号查询、解析、超时及测试保留，后续可独立验证后重新装配。没有修改 TUI 开关、SourceSelection、来源授权或 HTTP 契约；papers+web 的授权范围现在只执行其中已装配的 web provider，papers-only 明确报无可用来源，不偷偷改成 web。KB 不注册、不访问。

新增反例将 arXiv 构造器替换为立即失败，证明共享运行链路及独立 phase 均不构造它，且默认公开来源只产生一次 Bocha 调用；paper-only 零外部调用。CLI 隔离真实 PG/MinIO + 受控模型/搜索验证五个查询只入账五次 search，保持真实空结果 Gap、缺 analyze worker 明确失败和可 dump 的 seq=14，不声称完整业务已跑通。

首次集成测试因执行沙箱禁止本机 TCP 而 setup 失败；获准在沙箱外重跑后，`test_mono_cli_run.py` **9 passed in 16.71s**。搜索契约与 runtime 单测首次 **65 passed**，新增独立 phase 注册反例后 **66 passed in 1.22s**；修改代码 Ruff 与 diff 检查通过。未进行本批公网 arXiv/Bocha 调用，不把受控 provider 测试称为真实搜索验收。旧论文失败证据保留，不改写为成功。
