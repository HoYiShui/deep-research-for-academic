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

## 2026-10-07：真实 plan→论文 research，PDF 分支与失败诊断（仍未验收）

输入 [十字段冻结 Brief](t028-paper-brief.json) 是操作者选择的调试输入，不冒充 HTTP Clarify/确认记录。仅选择 `papers`，冻结 Parser 为 `dr4a-mineru-4.0.10-standard-v1`，使用显式准备好的本地模型目录；不自动下载权重、创建 bucket 或修改 PG。

在 backend 执行：

```bash
PARSER_VERSION=dr4a-mineru-4.0.10-standard-v1 MINERU_MODELS_DIR=/Users/hoyishui/.cache/dr4a/mineru-4.0.10-20261007 uv run --no-sync python -m scripts.verify_cli_plan --brief ../specs/004-backend-mono-alignment/evidence/t028-paper-brief.json --sources papers --real --record ../specs/004-backend-mono-alignment/evidence/t028-paper-plan-real.json
MINERU_MODELS_DIR=/Users/hoyishui/.cache/dr4a/mineru-4.0.10-20261007 uv run --no-sync python -m scripts.verify_research_phase --state ../specs/004-backend-mono-alignment/evidence/t028-paper-research-input.json --real --record ../specs/004-backend-mono-alignment/evidence/t028-paper-research-diagnostic-real.json
```

- [真实 plan](t028-paper-plan-real.json)：退出 0，五章、15 子问题、6 ClaimSpec；真实模型一次，input/output tokens 528/13189。无搜索/Fetch/PG 写入。
- [research 输入](t028-paper-research-input.json)：完整使用该真实 plan 的 state，仅将阶段标记改为 research，未手工改计划、Brief 或冻结配置。
- [首次 research](t028-paper-research-real.json)：退出 3，`all_search_sources_failed`；当时 CLI 丢失失败执行的诊断/部分状态，不能据此判断全部细节。
- [补齐诊断后的重试](t028-paper-research-diagnostic-real.json)：退出 3。第一条查询的 arXiv 第二次尝试成功，下一查询两次 `search_unavailable`；共四次搜索尝试、四次 Fetch、一真实模型调用，input/output tokens 253/426。保留两个已完成单元、一个实际下载并解析的 Source、零 Evidence/Claim/Observation，不生成 Report，也没有持久 Run。
- 成功下载的 Source 实际为不相关的 `Post-quantum hash functions using SL_n(F_p)`（arXiv:2207.03987v3），不是目标 Transformer 论文；模型返回空事实是有效的相关性拒绝，不伪造引用。其余三次 Fetch 为 `fetch_unavailable`，候选/原文块有界选择的降级保留在 state 中。尚无 Evidence，因此该重试未进入逐 Evidence 原文审计。

发现的业务缺陷：正式 query 单元直接把自然语言 `sub_questions` 交给 arXiv 的 `all:` 检索；第一条子问题含 arXiv 编号、caption/hash 等校验要求，却返回无关论文。网络失败与检索表达错误必须分别处理。`retrieval_anchors` 目前仅用于正文块排序，不用于搜索词生成。T028 保持未完成。

防数值误读：表格 Observation 必须匹配完整可见单元格；禁止截取 `3.3 ·` 的系数，禁止把 HTML `10<sup>18</sup>` 拼成整数 `1018`。实际 MinerU Table 2 的拆列缺陷仍保留在原 Evidence/Parser 记录中，未手工修补；含糊值数值为 null。完整行列归属、单位/协议绑定与数值比较尚待验收。

CLI 失败输出保留最后成功合并的本地 state/delta/events、失败单元 ID、debug 用量和各源安全错误码；不是持久 checkpoint。前置校验和未分类 SDK 异常仍走统一安全错误输出，不泄露异常正文。新增三类故障测试覆盖已完成单元保留、close 先于单一 JSON 输出、输入文件和 Run 元数据不变。

回归：全量 883 项通过（157.92s）；执行期间仅去重等价 whitespace helper，随后定向 CLI/抽取 28 项通过（6.57s）。Ruff check/format、git diff --check 通过。真实 research 失败不由这些受控回归结果替代。
