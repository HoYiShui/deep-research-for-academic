# Quickstart：后端调试 CLI

> Phase 1 输出。用最小路径验证 CLI 工作。CLI 复用 001 号 feature 的后端（`backend/`），默认 fake 模式不依赖任何外部服务。

## 前置

- Python 3.11+，`backend/` 依赖已装（`uv sync`）
- fake 模式（默认）无需 docker / 密钥；真实模式需 `doctor` 通过 + `.env` 配置

## 启动

```bash
cd backend
python -m cli --help
```

## 典型调试循环

```bash
# 1. 环境体检（真实模式前先跑，确认不是环境问题）
python -m cli doctor --json

# 2. 确定性单 agent 调试：改了 critic，只跑 review 这一刀
python -m cli slice review --input state.json --fake --seed 42 --json
#    退出码 0 = 对了；退出码 1 = stderr 一行错误

# 3. 全链路闭环（fake，秒级）
python -m cli run "compare transformer vs CNN intrusion detection" --fake --json

# 4. 跳过 clarify，直接喂冻结 brief 进 pipeline（query 省略，只给 --brief-file）
python -m cli run --brief-file brief.json --fake --json

# 5. 卡住时看 state（dump 是 real-mode，读 PG 快照，需先有真实 backend 跑出过快照）
python -m cli dump <session_id> --json

# 6. 单独验证 KB
python -m cli ingest paper.pdf --json
python -m cli search "intrusion detection" --json
```

## 输出约定

- 退出码：0 成功 / 1 研究失败 / 2 用法错 / 3 环境错
- `--json`：stdout 是单个 JSON 对象；不带 `--json` 为人类可读
- `--verbose`：LLM prompt/response 走 stderr，不污染 stdout
