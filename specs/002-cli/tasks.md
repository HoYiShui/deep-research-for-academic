# Tasks: 后端调试 CLI

**Input**: `/specs/002-cli/`（spec.md / plan.md）

**设计原则**：

- **契约先行**：先定输出契约（退出码 / stdout-stderr 分流 / --json）+ 确定性容器 → 再逐个命令
- **[P] = context 隔离**：每个命令自包含，只依赖冻结的输出契约
- **完成定义**：有测试证明它对了，且构建仍是绿的

## 任务 DAG（概览）

```text
T001 CLI 骨架 + 输出契约 + 确定性容器（--fake --seed）
        │
   ┌────┼─────┬─────┬─────┐   ← 契约冻结后，全部独立分支（context 隔离）
   ▼    ▼     ▼     ▼     ▼
 doctor  run   slice  dump  ingest+search
```

## Phase 1：CLI 骨架（收敛点）

[x] - [x] T001 实现 `backend/cli/` 骨架（`__main__.py` argparse 入口 `python -m cli`）+ 输出契约（退出码 0/1/2/3、`--json` stdout、`--verbose`/`--quiet`、stderr 日志）+ 把 `--seed` 确定性加进 `infrastructure/fake.py`（CLI 只注入 seed，不另造 fake）+ `--no-fake` 开关 + 单测 `tests/unit/test_cli_output.py`

**Checkpoint**: 输出契约冻结 + 确定性容器就位 → 命令可开始

## Phase 2：命令（[P] 独立）

[x] - [x] T002 [P] 实现 `doctor`（体检 PG / Milvus / MinIO / 模型权重 / env，任一不通过→退出码 3）+ 单测 `tests/unit/test_cli_doctor.py`
[x] - [x] T003 [P] 实现 `run`（全链路 clarify→pipeline→report；`--brief-file` 跳过 clarify、`--answers` 罐头答案、`--max-iterations`）+ 单测 `tests/unit/test_cli_run.py`
[x] - [x] T004 [P] 实现 `slice <phase>`（`--input state.json` 喂罐头 state，只跑单个 phase；复用 orchestrator 执行单 phase 的那段，必要时抽共享 `run_phase(phase, state, emit)`）+ 单测 `tests/unit/test_cli_slice.py`
[x] - [x] T005 [P] 实现 `dump <session_id>`（从 phase_snapshots 读 state 打印）+ 单测 `tests/unit/test_cli_dump.py`
[x] - [x] T006 [P] 实现 `ingest` + `search`（KB 单独入库/检索）+ 单测 `tests/unit/test_cli_kb.py`

**Checkpoint**: 6 个命令全部有测试，全部测试绿

## Dependencies & Execution Order

- **T001**：无依赖，最先做；冻结输出契约 + 确定性容器后分叉
- **T002–T006**：依赖 T001；互相独立（[P] = context 隔离）

## 完成定义

每个任务 =「有测试证明它对了，且构建仍是绿的」；不是「代码写完了」。
