# 文档目录（docs/）

本目录存放面向人阅读的项目文档，与规范层（`specs/`）和机器层（`.specify/`）互补：

- `specs/` — 功能规格（WHAT，验收标准）
- `docs/contracts/` — 输入/输出契约参考（详细版，供实现参考）
- `docs/cases/` — 端到端案例（完整期望输出，作为系统调试的 diff 基准）
- `docs/book/` — mdbook 构建产物（静态站点，已 gitignore）

## contracts/ — 输入/输出契约

| 文件 | 内容 |
|---|---|
| [researchbrief.md](contracts/researchbrief.md) | ResearchBrief 契约：归一化 7 要素、10 字段、术语对照、Clarify 原则 |
| [researchprofile.md](contracts/researchprofile.md) | 4 类任务：决策问题、专属字段、主要交付 |
| [report-skeleton.md](contracts/report-skeleton.md) | 统一报告骨架（完整 markdown）+ 3 个任务专属模块 |

## cases/ — 端到端案例（gold 报告）

| 文件 | 任务类型 | 领域实例 |
|---|---|---|
| [case-1-idea-exploration.md](cases/case-1-idea-exploration.md) | 选题构思（idea_exploration） | 内部威胁检测 |
| [case-2-method-differentiation.md](cases/case-2-method-differentiation.md) | 方法差分（method_differentiation） | 内部威胁检测建模路线 |
| [case-3-evaluation-design.md](cases/case-3-evaluation-design.md) | 实验与主张验证（evaluation_design） | 风险分数 + 事件排序方法 |

每个 case 的结构：**① 原始 query → ② ResearchBrief（10 字段）→ ③ 完整报告**，末尾附
**「审查评注」**（记录引用核验结果与待修问题）。

它们的作用是给 Agent 系统当 **diff 基准**：系统跑完后，把产出与这份 gold 报告对照，定位
"差在哪个章节、哪条结论、哪个证据"。

> 注：这些 case 是**期望输出的完整报告**，不是评测打分用的 `TaskPackage` / `gold_units`
> （见 `docs/architecture/07-evaluation.md`）。后续做离线评测时，可由此细化成评测 fixture。

## mdbook 静态展示

本目录已配置 mdbook（`book.toml` + `SUMMARY.md` + `theme/custom.css`）。用法：

```bash
cd docs && mdbook serve    # 本地预览（热更新），默认 http://localhost:3000
cd docs && mdbook build    # 构建静态站到 docs/book/
```

- 前置：需先安装 mdbook（macOS 可 `brew install mdbook`；有 Rust 工具链则 `cargo install mdbook`）。
- 新增文档后，在 `SUMMARY.md` 里登记章节即可出现在目录里。
- 构建产物 `docs/book/` 已加入 `.gitignore`，不入库。
