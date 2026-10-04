# Implementation Plan: pi-tui 端到端验证客户端

**Target Branch**: `feat/pi-tui` | **Date**: 2026-10-04 | **Spec**: [spec.md](spec.md)

## Summary

新增根目录 `tui/` 独立 Node/TypeScript 客户端。它以 `@earendil-works/pi-tui` 的 `TuiMainScreen` 建立可保留 scrollback 的 DR4A 调试时间线，经公开 HTTP/SSE 完成 Clarify、Brief 确认、pipeline、报告和诊断命令。v1 使用匿名开发 profile，不实现登录。

## Technical Context

**Language/Version**: TypeScript (ESM), Node.js >=22.19
**Primary Dependencies**: 固定版本的 `@earendil-works/pi-tui`；Node 原生 `fetch`、`ReadableStream` 与 `node:test`
**Storage**: N/A；只保留当前进程会话投影
**Testing**: `node:test`；本地 HTTP mock server 验证请求/SSE，组件/交互测试验证时间线
**Target Platform**: macOS/Linux 开发终端；不监听端口
**Project Type**: 根目录独立 Node CLI 包，消费 Python 后端公开 API
**Constraints**: 不 import Python 内部模块；不实现认证；SSE 只在 `ready` 后连接

## Constitution Check

*GATE: Passed.*

- **真实性与可溯源**：只显示后端实际响应、SSE 与错误，不虚构 Brief 或完成状态。
- **测试先行**：API/SSE 与关键交互均有 Node 测试；完成前通过测试与 typecheck。
- **安全与数据保护**：本阶段不处理密码、cookie 或 token。
- **可部署工程化**：TUI 是可独立安装、运行、测试的根目录 Node 包，不是一次性脚本。

## Project Structure

```text
tui/
├── package.json
├── tsconfig.json
├── src/
│   ├── main.ts            # 参数、API URL 解析、TuiMainScreen 生命周期
│   ├── api-client.ts      # HTTP、错误、SSE、health 的唯一适配层
│   ├── app.ts             # DR4A 状态机与时间线编排
│   └── components/        # status、timeline、brief-actions、composer
└── test/
    ├── api-client.test.ts
    └── app.test.ts
```

**Structure Decision**: `tui/` 与 `backend/` 同级，显式表达两种运行时和唯一 HTTP/SSE 边界。pi-tui 负责通用终端能力；`src/` 只拥有 DR4A 语义、API 投影和少量组件。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| 第二运行时（Node/TypeScript） | 直接复用 pi-tui 成熟交互体验 | Python Textual 要自行承担更多终端 UI 设计与样式维护 |
