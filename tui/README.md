# DR4A 调试 TUI

根目录的 pi-tui 是 HTTP/SSE 客户端；不导入 Python Agent，不自动确认 Brief。契约以 [mono API](../docs/mono/api-contract.md) 为准。

## 启动

后端（在 backend 中）：

```bash
uv run python -m scripts.debug_backend
```

读取 backend/.env，但连接同一 PostgreSQL 实例上的独立 `dr4a_debug` 数据库；不存在时创建，空库初始化 mono schema。**不修改 .env，不迁移/清空原数据库，不管理 Docker volumes。** PG 账号需有创建数据库权限。默认 manual：真实 Clarify/确认/状态/CLI dump 可用，确认后 Run 排 ready，等待执行器，不能称研究已开始。维护扫描仍处理取消、失租和排队超时；默认排队超过30分钟会 failed/queue_timeout，可按契约显式 resume，不自动执行研究。

要由后端执行现有真实 plan/research，显式开启（会调用收费模型/搜索）：

```bash
uv run python -m scripts.debug_backend --execute --parser html
# PDF-only 模式：需先安装 parser 可选依赖，并显式准备本地权重
MINERU_MODELS_DIR=/你的/已准备模型目录 uv run --no-sync python -m scripts.debug_backend --execute --parser pdf
```

开发执行器复用正式 RunDriver、PG checkpoint/预算/cache。当前只注册 plan/research；analyze/write/review 尚未实现时明确 failed，保留最后 checkpoint，不退回 fake，不生成空报告。修改/扩展 worker 注册在 `backend/application/debug_runtime.py`；Agent 本身仍在原实现位置。HTML/PDF 分支目前不是混合格式自动路由，PDF 仅支持已验证的 macOS 隔离配置。

必须先准备 MinIO bucket；此入口不自动创建 bucket/下载权重。KB、登录、附件、部署能力不属于本次调试路径。已有匿名 mono 后端也可以直接使用，无需该辅助入口。

TUI（在 tui 中）：

```bash
npm ci
npm run dev
# 可选：DR4A_API_URL=http://127.0.0.1:8011 npm run dev
# 或 npm run dev -- --api-url http://127.0.0.1:8011
```

默认后端 127.0.0.1:8000；环境变量优先于命令参数。后端辅助入口可用 `--port 8011`。TUI 不发送 cookie/token；401 会提示目标后端开启了认证。

## 操作

- 输入 query 创建研究；ask 时输入回答，confirm 时输入反馈会退回修改。
- `/confirm` 或 Ctrl+Enter：显式确认当前版本。Ctrl+R 提示反馈输入方式。
- `/patch {"scope":"…"}`：ask 阶段明确补齐字段，自动澄清达到上限后仍可使用。
- `/sources papers` 或 `/sources papers,web`：创建前设置来源；先 `/new`，不更改已冻结来源。
- `/open <session UUID>`：GET 恢复 ask/confirm/运行状态，不新建或恢复执行。
- `/status`、`/watch`：读持久状态／重新订阅。SSE 断开后有限次 GET+重连，不发送启动/恢复请求。只按事件 ID 去重，同 seq 的不同 progress 保留。
- `/cancel`：请求服务端取消。接受请求不等于已停止；观察状态。manual 模式也运行维护扫描器，可完成无有效执行租约的取消，但不领取研究任务或调用 Agent；若其他进程仍持有有效租约，等待其安全停止或租约过期。
- `/resume`：先读取最新状态，仅失败且允许恢复时发送最新 checkpoint_seq。
- `/retry`：显式重发上次网络/可重试故障请求，保留原 body/version/幂等键；不自动重试变更。409 会刷新状态，不偷偷同意新 Brief。
- `/report`：只读报告 Markdown；failed/cancelled 不自动取报告。`/session` 显示 CLI 提示。
- `/new`、`/connect <origin>`：关闭旧订阅/清本地上下文，不取消旧服务端任务。Ctrl+C 同样只退出本地。

## TUI → CLI 阶段调试

先在backend运行`uv run python -m cli doctor --scope research --debug-db --json`检查独立调试库与MinIO；通过只证明有限依赖检查，不保证模型/Parser或完整研究可用。普通后端库去掉`--debug-db`。

开发执行器运行research时，事件时间线会显示`query_started`、`query_completed`和`section_completed`，可按unit_id/section_id定位当前工作。完成指该单元Checkpoint已提交，不保证获得Evidence或研究结论成立；进度计数包含query与coverage单元。已提交单元恢复后不会伪装成新查询，断线重连也不会补播历史progress。

在 backend 中，用 TUI 的 session UUID：

```bash
uv run python -m cli dump <UUID> --debug-db --json > dump.json
jq '.state' dump.json > state.json
uv run python -m cli phase plan --state state.json --real --json > plan-result.json
jq '.state | .phase = "research"' plan-result.json > research-state.json
uv run python -m cli phase research --state research-state.json --real --json > research-result.json
```

普通后端库去掉 `--debug-db`。dump 需要已经确认并有 checkpoint 的会话；ask/confirm 仍用 TUI/HTTP。phase 输入严格校验阶段前置；本地 phase 结果不写回原会话、不推进其 Run、不改预算。改变 phase 标记只用于准备下一阶段调试输入，不能把缺少前置的阶段强行运行。包含既有 Source 的独立 debug state 仍可能发生原文命名空间冲突，不能当持久 Run 恢复工具。

phase 也接受 dump/phase 的 JSON 结果外壳，可直接 `--state dump.json` 调试 plan；真正校验的是其中的 canonical state，不使用外壳 status 冒充成功。运行下一阶段仍需明确准备正确 phase 标记与前置。

上述 shell 重定向是用户操作示例；结果含公开/私有研究内容，勿随意提交到 Git。错误结果可能有最后合并 state；先看 status/error，不把失败结果当成功报告。

## 验证范围

`npm test`、`npm run typecheck` 验证客户端控制逻辑。后端 `tests/integration/test_tui_live_http.py` 使用真实 TCP FastAPI/PG 与 TypeScript 客户端验证多轮/退回/确认/SSE/CLI dump/取消终态，但模型受控；不是报告或 Research 质量验收。

同一集成集还验证运行中取消、后端SIGKILL后重新打开会话、显式resume保留Run/seq、query进度与受控报告读取（真实PG/MinIO，模型受控）。它不验证终端布局或真实报告质量；`completed`与`review_verdict`仍必须分开理解。

`tests/integration/test_tui_terminal.py`另启动真正`src/main.ts`和pi-tui到POSIX伪终端，通过键盘输入验证Brief展示/显式确认/取消/CLI提示，以及SSE query进度和受控报告渲染；它不评价终端样式或真实报告质量。可在backend运行：

```bash
uv run --no-sync pytest -q tests/integration/test_tui_terminal.py tests/integration/test_tui_live_http.py
```

底层HTTP客户端的SourceSelection类型与mono三种类别一致；交互命令`/sources`仍只支持papers/web，不提供KB调试界面，也不据此宣称后端已支持KB。
