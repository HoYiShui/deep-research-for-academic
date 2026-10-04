# Tasks: pi-tui 端到端验证客户端

**Input**: `spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/tui-client.md`

**Important**: 旧 Textual/Python 实现不符合本任务，以下任务从零开始，所有项目均未完成。

## Phase 1: Root Node Package

- [x] T001 在根目录创建 `tui/package.json`、`tui/tsconfig.json` 和 TypeScript ESM 开发脚本。
- [x] T002 固定 `@earendil-works/pi-tui` 版本，并记录 Node.js >=22.19 前置条件。
- [x] T003 创建 `tui/src/main.ts`，实现 `DR4A_API_URL > --api-url > 默认值` 的 API URL 解析。

## Phase 2: HTTP/SSE Foundation

- [x] T004 在 `tui/src/api-client.ts` 实现匿名 JSON 请求、标准错误解码、`/health` 与 SSE 帧解析。
- [x] T005 在 `tui/test/api-client.test.ts` 用本地 HTTP mock server 验证路径、body、错误、SSE 顺序和无认证 header/cookie。
- [x] T006 在 `tui/src/app.ts` 定义 ResearchContext、TimelineEntry 与 `ask/confirm/ready/done` 状态转换。

## Phase 3: User Story 1 — Clarify 与 Brief (P1)

- [x] T007 以 `TuiMainScreen` 建立顶部状态、单一滚动时间线和底部 composer。
- [x] T008 实现初始 query、Clarify 回答与 API 响应的顺序追加。
- [ ] T009 实现 Brief 语义卡片及鼠标/键盘等价的确认、修改反馈动作。
- [ ] T010 在 `tui/test/app.test.ts` 覆盖 `ask → ask → confirm → ready` 与确认前不建立 SSE。

## Phase 4: User Story 2 — SSE、报告与取消 (P1)

- [ ] T011 实现 `ready` 后 SSE reader、已知/未知事件、error/断开和 `done` 后报告读取。
- [ ] T012 实现 `/status`、`/report`、`/cancel`，并区分取消响应和后续状态。
- [ ] T013 测试 SSE error/断开、报告未就绪和取消。

## Phase 5: User Story 3 — 调试与连接切换 (P2)

- [ ] T014 实现 `/session`、`/new`、`/connect <api-url>` 及安全切换语义。
- [ ] T015 测试 `/connect` 失败保留、成功清空和时间线/状态条输出。

## Phase 6: Verification

- [ ] T016 运行 TypeScript typecheck、lint、Node 测试和 `git diff --check`。
- [ ] T017 以启动中的本地匿名 backend 进行真实 HTTP/SSE E2E；记录后端契约偏差，不以 mock 替代它。
- [ ] T018 更新本目录 quickstart 的最终精确命令和验证结果。
