# US3 真实 research CLI 验证

## 2026-10-06：公开 HTML 原文闭环（T021/T027/T028 部分）

运行环境使用现有 MinIO 与 `.env` 的真实模型/Bocha 配置；未调用 PG，也未领取或修改既有 Run。`phase research` 已注册正式 research worker，fake 搜索只生成明确 Gap。

命令（在 backend 中）：

```bash
uv run python -m scripts.verify_research_phase --state ../specs/004-backend-mono-alignment/evidence/t028-web-input.json --real --record ../specs/004-backend-mono-alignment/evidence/t028-web-storage-real.json
```

输入是手工构造的五章计划，不是已验收的真实 plan 输出。唯一 query 为 `site:arxiv.org 1706.03762 Attention Is All You Need`，冻结来源仅 web，parser 为 `dr4a-html-v1`。

实测：

- 真实 CLI 返回成功状态，阶段仍为 research，不生成 Report；探针因为论文/PDF条件未满足，退出码为 1。
- 搜索尝试 1 次，Fetch 调用 4 次，真实模型调用 2 次，模型报告 input/output tokens 为 6703/11016。Fetch 调用次数不代表成功下载次数。
- 产出 2 个 Source、7 个 Evidence、9 个 Claim、0 个数值 Observation。
- 重新从 MinIO 读取原文并解析，7 个 Evidence 的原文 hash、真实章节/行位置与摘录范围全部通过审计。其中正文来自 `https://arxiv.org/html/1706.03762v7`，没有虚构 PDF 页码。
- 本次原文对象独立范围为 `research-content/329afba9-7658-44fa-baa1-c19ea15a90bb/`，不是输入快照的 Run ID；保留对象供回链审计。
- 保留候选/原文块有界选择降级，以及 PDF `parser_not_configured`/另一 PDF Fetch 失败；没有以摘要或缺失 PDF 伪造数值观察。

完整输入和输出见 [输入](t028-web-input.json) 与 [成功原文审计但未满足完整验收的记录](t028-web-storage-real.json)。T028、T027、T021 均不勾选：尚缺真实 plan→research、PDF、补查/追溯及持久 Run 装配。

前两次失败记录也保留：

- [第一次](t028-web-real.json)：query 是 `Attention Is All You Need Transformer attention arxiv original`（与当前输入文件不同，结果中的计划保留当时 query）；前四个博客/GitHub候选被 Fake-IP/SSRF 检查拒绝，只有 Gap，零模型调用/原文。
- [第二次](t028-web-site-real.json)：已收窄 arXiv query，但 MinIO 配置的 `deepresearch` bucket 不存在，CLI 返回 `content_missing`，退出码 3。只读检查确认 bucket 列表为空后，显式创建配置的 bucket；未修改 `.env`、PG 或 Docker volumes，再运行得到上述闭环结果。

遗留边界：CLI debug 每轮用独立原文范围，含既有 Source 的快照可能因 immutable content key 冲突拒绝合并；不能当作持久 Run 恢复证据。审计探针不会删除本轮原文对象。

回归：全量测试 849 项通过（146.35s）；随后补充的未配置 PDF Parser 前置拒绝测试连同 CLI 其余三项定向测试 4 项通过（4.81s）。新增测试不包含在上述全量 849 的计数内。Ruff check/format 与 git diff --check 通过。真实供应商证据和受控/fake 测试范围分别记录，不互相替代。
