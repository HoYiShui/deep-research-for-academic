# Implementation Plan: 后端调试 CLI

**Branch**: `002-cli` | **Date**: 2026-10-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-cli/spec.md`

## Summary

给 deep-research-agent 后端加一个 CLI 调试入口（`python -m cli`），统一 doctor / run / phase / dump / ingest / search 六个命令。Agent 是一等公民：非交互、确定性（默认 fake + `--seed`）、结构化输出（`--json`）、退出码 + stderr 错误。CLI 只接收冻结 Brief，不承载 Clarify；复用 001 号 feature 的 application / domain / infrastructure 层，不新增研究能力。

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: stdlib `argparse`（CLI 框架，零额外依赖）；真实模式复用现有 fastapi / asyncpg / pymilvus / httpx / anthropic。

**Testing**: pytest + pytest-asyncio（章程：合并前必须通过测试）。

## Architecture

### CLI 模块结构

```text
backend/cli/
├── __init__.py
├── __main__.py        # python -m cli 入口，argparse 子命令分发
├── output.py          # 输出契约：退出码、--json/--verbose/--quiet、stderr 日志
└── commands/
    ├── doctor.py
    ├── run.py
    ├── phase.py
    ├── dump.py
    ├── ingest.py
    └── search.py
```

### 输出契约（核心，跨命令一致）

- **退出码**：`0` 成功 / `1` 研究失败 / `2` 用法错误 / `3` 环境错误（doctor 不通过）。
- **stdout**：结果本体。默认人类可读；`--json` 时是单个 JSON 对象 `{status, final_report?, error?, events?}`。
- **stderr**：错误 + 日志，一行一条、带时间戳。`--verbose` 时每个 LLM 调用的 prompt + response 也打到这里。
- **`--quiet`**：只打最终报告，压掉进度噪音。

### 确定性策略（默认 fake + --seed）

- `--seed` 的确定性加进 `infrastructure/fake.py`（测试与 CLI 共享同一套 fake，不另造）。
- `seed` 驱动 fake 的**可复现多样性**：同 seed 同输出、异 seed 异输出（`seed 42` 一种场景、`seed 43` 另一种），而非固定输出让 `--seed` 变成无操作。
- CLI 只做 seed 注入（`--seed N` 传给 fake 容器），不新增 fake 实现。
- 默认 fake（全内存、秒级、无依赖）；真实依赖用 `--real` 显式启用，经 `doctor` 确认后使用。

### 事件消费（复用 EventBus，不另加接口）

- 001 的 orchestrator 把事件**推进 EventBus 队列**（非 async generator 逐个 yield），router 那边 drain 这个 bus 序列化成 SSE。
- CLI 走同一条路径：`create EventBus → run orchestrator（推事件进 bus）→ drain 队列 → print`。与 router 的 drain 一致，零改动、零漂移。
- 不另给 orchestrator 加 async generator 接口——那会造第二个消费接口，回到「平行路径」的老坑。

### phase 的复用（避免平行路径）

- `phase <phase> --state state.json` 不自己 new 一个 agent 跑——那会与 orchestrator 跑的代码漂移。
- 复用 orchestrator 执行单 phase 的那段（同一 state 切分、事件发射、结果合并）。CLI 专有验证确保 state phase 匹配且具备最低前置字段；它不替代生产状态机。
- phase 输出完整 post-state、events 与顶层 state delta，供 Agent 判断发生了什么变化。

### dump 的 real-mode 定位与持久化副作用

- `dump` 读 PG 的 `phase_snapshots`，是唯一 real-mode 命令；fake 模式下无数据。
- 其余命令（run / phase / ingest / search）默认 fake 快速循环；dump 是 real 取证，需真实 backend 先跑出过快照。
- `run --real`（real 模式）会往 PG 写 sessions / briefs / snapshots，有持久化副作用；fake 模式全内存、无副作用。二者不对称，调试时注意别污染真实库。

### 与既有层的关系（依赖方向不变）

```text
cli（新入口）
  → application（orchestrator / session_service / knowledge_base_service / bootstrap）
  → domain（agents / machine / state）
  → ports ← infrastructure（fake 或 real 适配器）
```

CLI 是继 `interface`（HTTP）之后的第二个入口，与 HTTP router 平级，只往下调用，不新增业务逻辑。

## Constitution Check

| 章程原则 | 设计如何满足 |
|---|---|
| I. 真实性与可溯源 | dump/run 读的是 phase_snapshots 真 state，不编造 |
| II. 测试先行 | 每个命令带 pytest；fake 模式可单测 |
| III. 安全与数据保护 | `--verbose` 只打 prompt/response，不打密钥 |
| IV. 成果保护与可回溯 | 复用 snapshot，不覆盖已有成果 |
| V. 可部署工程化 | stdlib argparse，无额外部署负担 |

## Project Structure

见上文 Architecture 的 `cli/` 结构。命令拆解与实现顺序见 `tasks.md`。
