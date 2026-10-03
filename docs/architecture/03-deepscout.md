# 面向学术研究生命周期的 DeepScout：递归检索、证据追溯与主张聚合

> 状态：终态重构设计草案。本文以现有 `DeepScout` 的章节检索、网页/本地搜索、递归 Query 和事实指纹为原型锚点，定义其迁移到网络安全学术研究后的职责与数据契约；不代表以下能力已经在当前原型主链路中实现。

## 1. 设计结论

DeepScout 的职责不是尽可能多地搜索网页，而是围绕 `SectionPlan` 中的研究论断，从论文、官方研究资产和本地资料中收集可定位证据；在原始信源或关键条件缺失时做受限追溯，并将资料归档为可供 Critic 校核、供 Writer 引用的 Claim–Evidence 图。

```text
SectionPlan
  → 按证据需求路由检索源
  → 发现候选资料
  → 读取高价值正文或 PDF
  → 抽取可定位 Evidence 与 ClaimCandidate
  → 过滤重复片段、聚合同类主张
  → 检查主张覆盖缺口
  → 仅在缺口存在时定向追溯或补查
```

### 1.1 检索链路术语对照

| 业务语义 | 内部对象 | DeepScout 中的作用 |
|---|---|---|
| 研究目标 | `SectionPlan.objective` | 决定本章节需要支撑的研究决策。 |
| 研究论断 | `Claim` | 决定要收集何种支持、反驳或限制性证据。 |
| 检索子问题 | `SectionPlan.sub_questions` | 生成首轮论文、网页与本地检索的具体 Query。 |
| 关键检索锚点 | `retrieval_anchors` | 约束方法、数据集、协议、指标和标准等检索条件。 |
| 证据需求 | `evidence_requirements` | 决定优先追溯原始论文、数据集文档、官方代码或标准原文。 |
| 检索证据 | `Evidence` | 保存来源、页码/章节/表格等定位信息与原文摘录。 |
| 可回溯证据链 | `ClaimEvidenceLink` | 表达证据对论断的 `supports`、`refutes` 或 `limits` 关系。 |

多源佐证指不同原始来源的多条 `Evidence` 关联同一 `Claim`。它们应被保留为独立来源；只有 `source_id + location + quote` 相同的重复片段才过滤。

## 2. 原型中可复用与应迁移的能力

| 原型能力 | 当前实现事实 | 终态迁移 |
|---|---|---|
| 章节化检索 | 每次最多并发研究 3 个 `pending_sections` | 保留。输入改为包含主张、子问题和证据需求的 `SectionPlan` |
| 网页搜索 | Bocha 返回标题、摘要和 URL，主链路基于摘要提取事实 | 保留为官方研究资产检索，不用摘要承载关键结论 |
| 本地向量检索 | 固定 `knowledge_base` Milvus 集合 | 保留为上传论文、组内资料、已有实验记录和数据集卡的入口 |
| 递归 Query | `source_tracing_queries`、`follow_up_queries` 最多深度 2 | 收紧为原始信源追溯与关键条件补查 |
| 网页深读 | 有 `deep_read_url` 与正文抽取，但无主链路调用点 | 改为 `fetch_document`，读取网页正文或论文 PDF 并保留定位片段 |
| 事实指纹 | 数字和少量中文片段的 MD5 | 改为重复证据过滤与主张聚合两层模型 |
| 知识图谱 | LLM 抽实体关系，写入 `ResearchState` 并主要供 UI 展示 | 改为 Critic/Writer 可消费的主张—证据关系图 |
| 股票数据 | 根据公司名查询实时行情 | 删除，属于行业研究遗留能力 |

## 3. 三类资料源及其业务职责

### 3.1 `paper_search`：发现学术主证据

论文检索负责发现论文、综述和预印本候选。可通过 arXiv API、学术索引或本地论文库检索元数据；标题和摘要只用于筛选候选，不能单独承担关键技术结论。

典型输入：方法名、任务、数据集、协议、指标、研究问题。

典型输出：`paper_id`、标题、作者、年份、摘要、PDF/DOI 链接、初步相关性。

### 3.2 `web_search`：补足外部研究资产

网页搜索不承担论文发现主任务。它用于论文与本地资料库覆盖不到的外部研究资产：

- 官方代码、项目主页、复现说明与版本配置；
- 数据集卡、数据下载页、许可与字段说明；
- 安全标准、威胁框架、漏洞通告和机构技术文档；
- 有时效性的 benchmark 更新、开源实现维护状态与工具支持范围。

例如，论文检索找到一篇内部威胁检测论文后，网页搜索可追到官方仓库或数据集说明，核实其数据版本、时间切分和资源要求。

### 3.3 `local_search`：复用私有或已审阅材料

本地知识库用于用户上传的论文、课题组沉淀资料、已有实验记录、阅读笔记和数据集文档。其价值是将长期积累的、无法或不宜通过公开 Web 检索获得的材料纳入同一证据链。

它不应是强制依赖：若本次任务没有本地语料，DeepScout 仍可基于论文检索与网页检索工作。

## 4. `fetch_document`：候选资料转换为可引用 Evidence

首轮检索的摘要只回答“这份资料是否值得读”。`fetch_document` 只对高价值候选、或待验证主张缺少原文核验时调用：

- 对论文 PDF，提取实验设置、数据集版本、切分协议、基线、指标、结果和局限；
- 对代码仓库，提取真实输入、依赖、资源要求和配置；
- 对数据集文档，提取标签、规模、许可和适用边界；
- 对标准原文，提取适用范围与具体要求。

输出必须含来源定位，而不是仅存摘要：

```python
Evidence(
    id="E1",
    source_id="arxiv:xxxx.xxxxx",
    source_type="paper",
    location="p.7, Table 4",
    quote="支持或限制某一主张的原文片段",
    retrieved_via=["paper_search", "citation_trace"],
)
```

## 5. 受限递归：信源追溯与定向补查

递归扩展只能服务明确缺口，不应作为自由的网页扩张。

### 5.1 `citation_trace`：追到原始信源

当首轮资料是综述、博客或二手报告时，追溯其引用的原始论文、数据集论文、标准原文或实验资产。

```text
二手转述 / 综述
  → 原始论文
  → 数据集、代码或标准的官方资料
```

例如，综述称“某方法跨版本泛化更好”，系统须追到原始论文，核对数据版本、切分协议、指标和基线，而不是把综述句子作为结论依据。

### 5.2 `gap_fill`：补齐关键比较条件

当某个主张已找到机制说明，但缺少部署成本、日志依赖、训练信号、数据版本、评测协议、指标或局限时，只生成该缺口对应的补查 Query。

### 5.3 边界

- 每个主张最多产生有限数量的补查 Query；
- 默认最多两层追溯；
- 重复 Query、无新增 Evidence、预算耗尽或检索失败时停止；
- 未解决缺口写入风险清单，不能由模型常识补全。

## 6. 从“事实去重”到“证据去重 + 主张聚合”

### 6.1 原型事实指纹的限制

原型并不对完整事实文本做哈希。它先抽取前三个数字与前五段中文片段，再对截断结果做 MD5。该做法试图发现不同措辞的相似事实，但会丢失方法、立场和限制条件。

不同 URL 的同指纹事实会被直接跳过，因而可能丢掉独立佐证或限制性结论；同 URL 的重复事实反而可能再次写入。它只适合作为早期、低成本的近似重复过滤。

### 6.2 第一层：重复 Evidence 过滤

同一材料的同一位置被多条 Query 或多个章节反复命中时，只保留一份 Evidence：

```python
evidence_fingerprint = sha256(
    source_id + location + normalize(quote)
)
```

其中 `source_id` 可以是 DOI、arXiv ID、数据集版本、仓库 commit 或规范化 URL；`location` 为页码、章节、表格编号或文件行号。重复 Evidence 不重建节点，但可补充它关联的章节和主张。

### 6.3 第二层：规范化 Claim 聚合

不同来源的相同研究判断不应删除，而应挂到同一个 Claim。模型以 Schema 抽取 `ClaimCandidate`，程序规范化字段后生成稳定的 `claim_key`：

```python
ClaimCandidate(
    claim_type="empirical_comparison",
    subject="temporal_behavior_model",
    predicate="outperforms",
    object="feature_baseline",
    task="insider_threat_detection",
    dataset="CERT_r6.2",
    protocol="cross_version",
    metric="Recall_at_alert_budget",
)
```

```python
claim_key = sha256(json.dumps(normalized_claim_fields, sort_keys=True))
```

哈希只是稳定 ID；真正决定能否聚合的是结构化字段。方法、数据集、协议或指标不一致时，系统不强行合并，应将条件差异保留在 `Claim.conditions`（`status=limited`），并将其差异交给后续比较。

### 6.4 新资料的处理规则

```text
新资料
  ↓
同一 source_id + location + quote？
  ├─ 是：重复 Evidence，补充关联信息，不新增节点
  └─ 否：新建 Evidence
          ↓
    是否表达已有规范化 Claim？
      ├─ 是：关联到已有 Claim
      └─ 否：新建 Claim 或 ClaimVariant
```

## 7. Claim–Evidence 图

第一版不需要 Neo4j 或图数据库。`Claim[]`、`Evidence[]` 与关联表即可表达图关系。

```python
Claim(
    id="C1",
    text="时序行为表示路线在跨版本内部威胁检测中具有泛化潜力",
    conditions={
        "dataset": "CERT r6.2",
        "protocol": "cross_version",
        "metric": "Recall@alert_budget",
    },
)

Evidence(
    id="E1",
    source_id="arxiv:xxxx.xxxxx",
    location="p.7, Table 4",
    quote="……",
)

ClaimEvidenceLink(
    claim_id="C1",
    evidence_id="E1",
    relation="supports",  # supports / refutes / limits
)
```

`relation` 位于 Claim 与 Evidence 的关系上，而不是 Evidence 自身：同一 Evidence 可能支持一个主张，同时限制另一个主张。

```text
C1：时序行为表示路线在跨版本内部威胁检测中具有泛化潜力
├─ 条件：CERT r6.2 / cross-version / Recall@alert budget
├─ supports → E1：论文 A，第 7 页表 4
├─ supports → E2：官方复现仓库的实验配置
└─ limits   → E3：论文 B 指出结果依赖标签质量或固定日志源
```

## 8. Critic 与 Writer 如何消费该图

### Critic：检查主张覆盖与结论边界

Critic 对 ResearchBrief 中的每项研究论断检查：

- 是否存在原始或高等级来源；
- 数据集、协议、指标等成立条件是否明确；
- 是否只有二手转述，需不需要 `citation_trace`；
- 是否缺少比较维度或局限信息，需不需要 `gap_fill`；
- 是否存在相互矛盾的 Evidence，需要在报告中保留不确定性。

### Writer：仅基于关联 Evidence 写作

Writer 以 Claim 为单位读取关联 Evidence，而不从全局事实池自由取材。报告应明确写出结论的适用条件与限制，例如：

> 在 CERT r6.2 的跨版本协议下，论文 A 报告了……；该结果仍受标签质量和固定日志源的限制，不能直接外推至生产环境。
