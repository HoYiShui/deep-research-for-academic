# Quickstart：后端调试 CLI

> Phase 1 输出。用最小路径验证 CLI 工作。CLI 复用 001 号 feature 的后端（`backend/`），默认 fake 模式不依赖任何外部服务。

## 前置

- Python 3.11+，`backend/` 依赖已装（`uv sync`）
- fake 模式（默认）无需 docker / 密钥；真实模式需 `doctor` 通过 + `.env` 配置

真实模式在宿主机运行 backend，先启动基础设施：

```bash
docker compose --env-file backend/.env up -d
```

`backend/.env` 中的 host endpoint 应为 PostgreSQL `localhost:5432`、MinIO
`localhost:9000`、Milvus `http://localhost:19530`。生产容器使用内部服务名和端口，
由 `docker-compose.prod.yml` 覆盖，不复用这些 host endpoint。

## 启动

```bash
cd backend
python -m cli --help
```

## 典型调试循环

```bash
# 1. 环境体检（真实模式前先跑，确认不是环境问题）
python -m cli doctor --json

# 2. 确定性单阶段调试：改了 critic，只跑 review
python -m cli phase review --state review-ready.json --seed 42 --json
#    退出码 0 = 对了；退出码 1 = stderr 一行错误

# 3. Pipeline 闭环（fake 默认，秒级；Brief 必须已由 HTTP Clarify 冻结）
python -m cli run --brief frozen-brief.json --json

# 4. 要接真实依赖时，显式改用 --real
python -m cli run --brief frozen-brief.json --real --json

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
- `run` 不实现 Clarify；多轮对话通过 HTTP/API 或前端完成，冻结后的 Brief 才可交给 CLI。
- `phase` 的 JSON 输出包含完整 post-state、events 与顶层 `state_delta`；输入缺前置字段会以退出码 2 拒绝。
