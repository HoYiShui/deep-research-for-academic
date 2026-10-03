# Implementation Plan: 后端调试 CLI

**Branch**: `002-cli` | **Date**: 2026-10-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-cli/spec.md`

## Summary

给 deep-research-agent 后端加一个 CLI 调试入口（`python -m backend.cli`），统一 doctor / run / slice / dump / ingest / search 六个命令。Agent 是一等公民：非交互、确定性（`--fake --seed`）、结构化输出（`--json`）、退出码 + stderr 错误。复用 001 号 feature 的 application / domain / infrastructure 层，不新增研究能力。

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: stdlib `argparse`（CLI 框架，零额外依赖）；真实模式复用现有 fastapi / asyncpg / pymilvus / httpx / anthropic。

**Testing**: pytest + pytest-asyncio（章程：合并前必须通过测试）。

## Architecture

### CLI 模块结构

```text
backend/cli/
├── __init__.py
├── __main__.py        # python -m backend.cli 入口，argparse 子命令分发
├── output.py          # 输出契约：退出码、--json/--verbose/--quiet、stderr 日志
├── fake.py            # 确定性 fake 容器（--fake --seed）
└── commands/
    ├── doctor.py
    ├── run.py
    ├── slice.py
    ├── dump.py
    ├── ingest.py
    └── search.py
```

### 输出契约（核心，跨命令一致）

- **退出码**：`0` 成功 / `1` 研究失败 / `2` 用法错误 / `3` 环境错误（doctor 不通过）。
- **stdout**：结果本体。默认人类可读；`--json` 时是单个 JSON 对象 `{status, final_report?, error?, events?}`。
- **stderr**：错误 + 日志，一行一条、带时间戳。`--verbose` 时每个 LLM 调用的 prompt + response 也打到这里。
- **`--quiet`**：只打最终报告，压掉进度噪音。

### 确定性策略（--fake --seed）

- 复用 `infrastructure/fake.py` 的 FakeLLM / FakeSearch / FakeStateStore / FakeRetrieval / FakeExecution。
- `seed` 决定 fake 输出（同 seed 同输出），满足「改一处 → 跑一次 → 看结果」的可复现。
- 默认 fake（全内存、秒级、无依赖）；真实依赖为可选模式，经 `doctor` 确认后启用。

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
