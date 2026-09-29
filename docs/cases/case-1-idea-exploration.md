# Case 1: idea_exploration — 公开 CERT 数据上面向分析员的内部威胁检测选题

> 任务类型：idea_exploration（选题构思）
> 用途：端到端 gold 报告，作为后续 Agent 系统调试的 diff 基准。
> 生成方式：真实检索（web search + 读原文摘要/全文），逐条可回溯，未确证处显式标注「待核实」。

---

## ① 原始 Query

```text
我希望在公开 CERT 数据上做面向分析员的内部威胁检测研究。请梳理多源日志表征、
事件级溯源与跨版本泛化三个方向的已有证据，识别可在公开数据和有限算力下验证的
研究缺口，并提出 3 个候选研究问题。
```

---

## ② ResearchBrief（10 字段，冻结）

| 字段 | 取值 |
|---|---|
| task_type | idea_exploration |
| decision_goal | 在公开 CERT 数据 + 有限算力约束下，识别「多源日志表征 / 事件级溯源 / 跨版本泛化」三个方向中可验证的研究缺口，并据此提出 3 个候选研究问题、给出推荐优先级 |
| research_object | 面向分析员（可解释、可行动）的内部威胁检测；输入 = 多源行为日志，输出 = 用户风险分数 + 事件级定位/溯源 |
| scope | 公开 CERT Insider Threat Test Dataset（以 r4.2、r6.2 为主，r5.2 为辅）；有限算力（单机/单卡、CPU 可复现）；不涉及企业私有数据 |
| comparison_scope | 三个方向：① 多源日志表征（特征工程 / 序列 / 图 / 时序点过程）；② 事件级溯源（归因 / 攻击链重建 / provenance）；③ 跨版本泛化（r4.2→r6.2 迁移 / 未见用户 / 时间切分） |
| claims_to_verify | 每个方向在「公开数据 + 有限算力」下是否存在可验证缺口；哪些缺口已被覆盖、哪些仍是空白 |
| evidence_requirements | 官方数据集文档（SEI/KiltHub/figshare）、同行评议论文（含 DOI/页码）、公开数据集派生包（TRACE-IT）、系统综述 |
| conclusion_boundary | 结论止于「候选研究问题 + 可行性排序」粒度；不承诺任何生产环境检测效果；不给出具体算法实现或超参 |
| deliverable | 候选问题卡（3 张）+ 推荐问题与最小验证闭环 + 待验证风险清单 |
| assumptions | ① 公开 CERT 数据可用且可离线获取；② 算力 = 单机/单卡（CPU 优先可复现）；③ 「面向分析员」≈ 结果可解释、可溯源到事件、告警可行动；④ 不涉及私有 CERT 数据或未公开版本 |

---

# 公开 CERT 数据上面向分析员的内部威胁检测：三方向证据梳理与候选研究问题

## 0. Research Brief

- **研究类型**：idea_exploration（选题构思）
- **要解决的决策**：在「公开 CERT 数据 + 有限算力」下，判断「多源日志表征 / 事件级溯源 / 跨版本泛化」三个方向各自是否存在可验证缺口，并产出 3 个候选研究问题及推荐顺序
- **研究对象与范围**：面向分析员的内部威胁检测；公开 CERT 数据集（r4.2 为主、r6.2 为稀疏对照、r5.2 为中间态）；单机算力
- **结论边界**：止于候选问题 + 可行性排序；不承诺生产效果、不给算法实现
- **关键假设**：见上表 `assumptions`（公开数据可用、单机算力、「面向分析员」= 可解释可溯源可行动）

## 1. 问题定义与研究边界

### 1.1 目标问题

内部威胁检测在学术上已有十余年积累，但存在一个被反复指出的张力：**以「用户风险分数」为输出的检测器在基准上分数很高，却难以被安全分析员真正使用**——因为黑箱分数无法回答「这个用户具体做了什么、哪条日志是恶意的、我该查什么」。本报告要回答的决策问题是：在公开 CERT 数据与有限算力的硬约束下，围绕（a）如何把多源日志表征得更利于分析员理解、（b）如何把检测结果溯源到事件级、（c）如何评估跨版本/跨用户的泛化边界，这三条线索上**哪些缺口是真实存在、且能在公开数据上被验证的**。

### 1.2 系统、数据与威胁边界

- **系统边界**：企业终端与网络行为日志（登录、文件、可移动设备、邮件、Web 五类），加组织元数据（LDAP 角色/部门/主管）与心理测量（Big Five 人格）。
- **数据边界**：CERT Insider Threat Test Dataset（CMU SEI + ExactData，DARPA I2O 资助），合成数据。版本族 r1…r6.2，本报告聚焦 r4.2（1,000 用户 / 70 内鬼 / 3 场景，密集态）、r6.2（4,000 用户 / 5 内鬼 / 5 场景，稀疏态）、r5.2（2,000 用户 / 4 场景，中间态）。
- **威胁边界**：数据集内置的恶意场景——数据窃取（exfiltration）、知识产权/数据盗取、IT 破坏（sabotage）、以及 r5.2 起新增的横向移动+持续窃取（LMDE）。**本报告不覆盖**数据集未建模的威胁（如物理入侵、纯社交工程、无日志痕迹的恶意）。

### 1.3 本报告不回答的问题

- 不给出具体模型架构、损失函数或超参。
- 不承诺某方法在生产环境/真实企业日志上的检测率。
- 不对各论文的绝对分数做跨研究「谁更准」的硬排序（口径不一致，见 §2.3 与 §4.2）。
- 不评估真实 CERT/CSIRT 运营流程的合规性。

## 2. 证据基础与关键发现

### 2.1 检索范围与信源标准

- **信源分级**：① 官方数据集页（SEI 库 / KiltHub / figshare，DOI 10.1184/R1/12841247.v1）与原始论文（Glasser & Lindauer 2013）；② 同行评议论文（ACM CCS、IEEE TNSM/BigData、ACM Computing Surveys、Varstvoslovje 等，带 DOI/页码）；③ 公开派生数据集（IEEE DataPort TRACE-IT，DOI 10.21227/bdg8-0565）；④ 学位论文/预印本作补充（标注为灰文献）。
- **信源排除**：博客二手转述、无出处的排行榜数字、无法回链到原文的「AUC=…」摘要。
- **检索子问题**：分别覆盖「数据集事实 / 多源表征 / 时序与图方法 / 事件级溯源 / 跨版本泛化 / 分析员导向评估」六组，共约 10 次检索并交叉核对。

### 2.2 已确认事实（含来源定位）

以下事实由多个来源交叉确认，逐条可回溯；标注「单源」表示仅一个来源、可信度待提高。

**A. 数据集层面**

1. **数据源与生成方式**：CERT 数据集由 CMU SEI CERT 部门与 ExactData 合作、DARPA I2O 资助，属**合成数据**；生成器组合可观测行为模型、人因研究、以及「合理但未验证」的理论，用关系图（组织/社会结构）与资产图保证内部一致性（Glasser & Lindauer, SPW 2013, pp.98–104；DOI 10.1109/SPW.2013.37）。**合成性是其代表性局限的根源**。
2. **版本与规模（r4.2）**：1,000 用户，**70 名内鬼**，约 **3,270 万条事件**（单源数字为 32,770,227），时间约 501–502 天（约 17–18 个月），3 个场景：数据窃取（30 人）/ 知识产权盗取（30 人）/ IT 破坏（10 人）。多源交叉一致。
3. **版本与规模（r6.2）**：4,000 用户，**5 名内鬼**，约 **1.35 亿条事件**（单源 135,117,169），516 个工作日，5 个场景（每个场景 1 名攻击者）。r6.2 被系统综述明确称为「needle-in-a-haystack」（1:800 稀疏态），r4.2 为「dense needle」（1:14）。
4. **版本与规模（r5.2）**：2,000 用户，4 个场景（在 r4 基础上新增横向移动+持续窃取 LMDE）。内鬼数与事件数在不同来源不一致（99 内鬼 / 87.8M 事件为单源数字）——**待核实**。
5. **文件结构**：logon.csv（登录/登出）、device.csv（USB 等可移动设备 connect/disconnect）、file.csv（文件操作）、email.csv（邮件，含附件）、http.csv（visit/download/upload + url）；组织元数据 LDAP（角色/部门/团队/主管）；心理测量 psychometric.csv（Big Five：O/C/E/A/N）。真值在 `answer/` 文件（按场景记录版本、scenario、涉及文件、用户名、起止时间）。
6. **标签粒度与不平衡**：默认标签在 **user-day** 级（当天有恶意行为即标为威胁）；恶意事件占比极低——用户/天级约 0.4%–3%，事件级可低至 10⁻⁶（单源；不同来源口径略有差异，见 §2.3 口径说明）。Le & Zincir-Heywood 报告恶意样本占训练集约 0.5%、测试集约 0.2%（TNSM 17(1):30–44）。
7. **是否存在 r7**：本次检索**未发现 r7 版本**；检索到的版本族止于 r6.2。**待核实**（如需跨到最新版本，须在 SEI 官方页确认）。

**B. 多源日志表征方向（方向 1）**

8. **特征工程是主导范式，且被综述判定为「比模型复杂度更决定性能」**：Jurišić & Tomičić（Varstvoslovje 28, 2026）对 2013–2025 共 **131 篇**用 CERT 的同行评议研究做 PRISMA 系统综述，结论之一明确为「feature engineering is a stronger determinant of detection performance than model complexity」。
9. **会话/时间聚合 + 语义编码 + 心理测量是三类主流特征**：时序/统计聚合（day/week 多粒度计数）、NLP 语义编码（TF-IDF、Word2Vec、BERT 处理 URL/文本）、Big Five 人格融合。Le & Zincir-Heywood 的会话级特征在 r4.2 为 **107 维**、r5.2 为 **190 维**，分「用户活动特征」与「上下文特征」（TNSM 17(1):30–44）。
10. **序列深度模型代表**：Tuor et al.（arXiv:1710.00811，AAAI AICS 2017）提出 per-user 在线无监督 DNN/RNN，24h 窗口聚合 **408 个活动特征**，按预测误差打分，并把异常分数**分解到单个行为特征**以助分析员；**注意该文使用 CERT v6.2 而非 r4.2**（常见误记为 r4.2）。最佳模型下恶意事件异常分平均处于 **95.53 百分位**（原文报告，用于说明可降低告警工作量）。
11. **时序点过程代表**：Yuan, Zheng, Wu, Li（IEEE BigData 2019, pp.1343–1350；arXiv 1910.03171）提出层级神经时序点过程（seq2seq + marked TPP），同时捕获活动类型与多尺度时间（会话时长/间隔），实验表明「活动类型 + 多尺度时间」联合优于单信息。**注意该文为 2019 而非 2018**（常见误记）。
12. **图方法代表**：Liu et al.「Log2vec」（**ACM CCS 2019, pp.1777–1794，DOI 10.1145/3319535.3363224**，常见误记为 ESORICS 2018）把用户日志构造成异构图，随机游走 + word2vec 得到事件向量，聚类 + 阈值检测，**无需攻击样本**、适合不平衡；后续有 GNN/GCN 与「结构偏差」类方法（结构偏差 AUC ≈ 0.982、迁移鲁棒性下降 <2% 为单源/二手数字，**待核实**）。Log2vec 及其改进（log2vec++）的 AUC 0.86/0.93 与对比基线分数来自二手汇总，**待核实原文**。
13. **新兴方法**：LLM 把日志当语言处理；GAN 用于放大少数内鬼类（综述定位为 emerging）。

**C. 事件级溯源方向（方向 2）**

14. **核心缺口（被多篇工作显式承认）**：黑箱深度模型（128–256 维 GNN embedding、CNN-Transformer 如 CATE，二手来源称 r4.2 F1 96.18%，**待核实**）只输出二分类标签，**无法回溯到具体恶意事件**（如「02:17 复制 CAD 文件到 USB」）。
15. **会话级建模天然保留事件粒度**：把 logon–logoff 之间的离散事件聚合成「会话」，保留如 `n_afterhourallact`、`n_file`、`n_usb` 等 27+ 个可解释特征，从而支持事件/特征级定位（多篇近作采用）。
16. **攻击链/证据挖掘已有独立进展**：Ripple2Detect（Computers & Security, DOI 10.1016/S0167404825000768）建攻击证据库 + 知识图谱，按 login–logout 操作序列切分，用 BERT 对比学习做多步证据挖掘；MGDA（Computer Networks, S1389128625007728）用多视角掩码图自编码器 + TTP 规则做攻击场景重建；GETSUS 在 provenance 图上反向传播可疑度（数据泄露/内部威胁场景零漏报）；TransProv 用 Transformer 做攻击溯源重建。**但这些 provenance/攻击链工作多为系统级 provenance 或非 CERT 用户日志域**，与「CERT 五源用户行为日志上的事件级联合溯源」并非同一设定。
17. **分析员导向解释**：SHAP（树模型 TreeExplainer 精确 Shapley）/LIME/注意力权重是主流归因手段；有工作用 LLM 生成结构化「分析员笔记」并映射 MITRE ATT&CK（如 T1078 有效账户、T1005 本地数据、T1204.002、T1567.002 外传）。**但 SHAP 在内部威胁（相对网络入侵检测）应用仍被判定偏少**，且解释常被当作「事后报告组件」而非「训练/输出的一部分」。

**D. 跨版本泛化方向（方向 3）**

18. **系统性版本偏倚（最强证据）**：系统综述（131 篇）发现多数研究用 r4.2（70/1000，1:14 密集态），少数用 r6.2（5/4000，1:800 稀疏态），由此产生「performance bubble」——**分数虚高且无法外推到运营环境**；同时「无标准化 train/test 切分与负采样比」，跨研究不可比。
19. **跨版本性能退化有直接证据**：一项贝叶斯优化 XGBoost 研究在 r4.2/r5.2/r6.2 上实验，同版本 r4.2 F1=0.9655/AUC=0.9992、r5.2 F1=0.9000/AUC=0.9964；但在 r6.2 极端不平衡（至 800:1）下默认阈值**一个正例都不预测（F1=0.0000）**，而 AUC-ROC 仍达 0.9725——作者归因于概率校准问题而非学习失败；并报告跨版本评测出现显著性能退化，建议自适应阈值 + 领域适应（Security and Privacy, DOI 10.1002/spy2.70122；具体跨版本退化数字未在摘要中给出，**待核实原文表**）。
20. **迁移学习方法存在**：uOttawa 博士论文 DTITD（灰文献）用 Digital Twin + Transformer 变体，以预训练 LLM 上下文嵌入（BERT/RoBERTa/DistilBERT/XLNet）作为迁移机制处理不平衡，在 r4.2（密集）+ r6.2（稀疏）评测，并延伸出联邦 PETuning 的 FedITD。**属灰文献，未见独立复现**。
21. **专用于跨版本评测的数据集已出现**：TRACE-IT（IEEE DataPort, DOI 10.21227/bdg8-0565）从 r4.2/r5.2/r6.2 派生用户级行为特征（r4.2 = 109 维，r5.2/r6.2 = 108 维），明确用于跨版本评测（分类、概率校准、选择性预测、不确定性量化、解释稳定性、少数场景可靠性），并建议 **user-grouped 验证 + 报告 Macro F1 / balanced accuracy / ROC-AUC / ECE / Brier**。
22. **可迁移/可适应的已有技术路径**：Le & Zincir-Heywood（CISDA 2019）用可适应遗传编程（LGP），让已训练种群**适应扩展特征空间/新输出类**，减少从头重训；其 CNSM 2019 关注特征归一化与时序信息；IEEE CNS 2020 关注对抗属性。

**E. 分析员导向评估层面**

23. **可行动指标被长期忽视**：系统综述指出大多数研究用 AUC/F1，**很少报告检测延迟（detection delay）与告警预算召回（budget-based recall）**——而这两者才是分析员真正关心的运营指标。有 SIEM 集成原型用校准（Platt/isotonic）做分析员可控阈值与稳定告警预算（precision≈0.70 @ recall≈0.30，Device 日志）；有因果升级研究强调「lead time / early detection」应优先于分类指标。高误报被明确列为「浪费分析员资源、侵蚀项目支持」的来源。

### 2.3 研究论断与证据缺口（Claim ↔ 证据关系）

| 论断（Claim） | 证据定位 | 关系 |
|---|---|---|
| C1「CERT 是合成数据，代表性有限」 | Glasser & Lindauer 2013 pp.98–104（生成方法自述）；Jurišić & Tomičić 2026（依赖单一合成数据集限制外推） | **supports** |
| C2「r4.2 密集（70/1000）、r6.2 稀疏（5/4000）」 | 多源（SEI 派生页 + 综述 + TRACE-IT） | **supports** |
| C3「特征工程比模型复杂度更决定性能」 | Jurišić & Tomičić 2026（131 篇综述结论） | **supports**（单一强综述，非多独立实验） |
| C4「多源表征已从静态特征走到序列/图/时序点过程/LLM」 | §2.2 B 组（Tuor 2017；Log2vec CCS 2019；Yuan 2019；综述演进脉络） | **supports** |
| C5「黑箱深度模型无法事件级溯源」 | 多篇近作显式承认 + CATE 只输出二分类（二手，**待核实**） | **supports**（方向性明确，但「事件级不可溯」为工作自述+综述，非独立量化证据） |
| C6「事件级溯源（攻击链/证据挖掘）已存在，但多为系统级 provenance 或非 CERT 用户日志域」 | Ripple2Detect / MGDA / GETSUS / TransProv 各自设定 | **supports**（对「CERT 用户日志域缺口」是间接证据） |
| C7「跨版本泛化是真实缺口，且缺乏标准化评估协议」 | Jurišić & Tomičić 2026（无标准化切分、版本偏倚、性能虚高） | **supports**（最强证据） |
| C8「跨版本退化可归因于不平衡+校准，而非纯模型容量」 | XGBoost 研究（r6.2 默认阈值 F1=0 而 AUC 高） | **partial**（单篇、且 r4.2→r6.2 退化数字需核原文表） |
| C9「LLM 迁移可桥接密集/稀疏版本」 | DTITD 博士论文（灰文献，无独立复现） | **limits**（有方向、证据强度弱） |
| C10「分析员关心告警预算/检测延迟，而非仅 AUC/F1」 | 综述 + SIEM 原型 + 因果研究 | **supports** |

**证据缺口汇总（将转化为候选问题）**：

- **G1**：多源表征与「事件级可解释」之间的桥梁——已有表征要么黑箱（GNN/Transformer embedding）、要么是手工特征；**缺乏一个「在训练/推理内即可溯源到事件、且不牺牲检测能力」的统一表征**。
- **G2**：在 **CERT 用户行为日志域**上做「多源日志的联合事件级溯源/攻击链重建」的验证——provenance 路线多在系统级审计图，未在 CERT 五源日志 + answer 真值上做事件级对齐验证。
- **G3**：**跨版本泛化的失败模式归因 + 可复现评估协议**——已有证据显示退化存在，但缺「退化=不平衡 vs 分布漂移 vs 场景不重叠」的分解，以及标准化时间切分/未见用户切分协议。

## 3. 候选研究问题与可行性评估

### 3.1 已有工作与研究缺口

- **多源日志表征**：已走完「静态特征 → 会话聚合 → LSTM/RNN → 时序点过程 → 异构图嵌入 → LLM-as-language」的演进，且特征工程被证成主导因素。**缺口**：表征的「事件级可解释性」长期作为事后归因（SHAP/注意力）而非训练内属性；面向分析员的表征是否能在「不牺牲检测性能」前提下把风险分数直接分解到事件/特征，未被系统回答。
- **事件级溯源**：攻击链/证据挖掘（Ripple2Detect）、provenance 重建（MGDA/GETSUS/TransProv）已有进展，但**设定多为系统级 provenance 或非 CERT 用户日志域**；在「CERT 五源日志 + answer 场景真值」上做事件级联合溯源、并以事件级 recall@k 量化的验证几乎空白。
- **跨版本泛化**：系统性版本偏倚 + 无标准化协议 + 单篇跨版本退化证据，是**证据最确凿、验证最便宜**的缺口；缺「失败模式归因」与「可复现协议」。

### 3.2 候选问题卡

| 候选问题 | 可验证假设 | 所需数据/资源 | 新颖性风险 | 可行性 |
|---|---|---|---|---|
| **RQ1（方向 1）** 面向分析员的事件级可溯源多源日志表征：能否在保持检测性能的同时，把用户风险分数「训练内」分解到具体日志事件/特征，使分析员直接定位异常来源？ | H1a：相比 Tuor 式 per-user 序列模型 / 黑箱 GNN，事件级可溯源表征在**同等告警预算下**对真实恶意事件的 hit@k（top-k 命中）显著更高；H1b：其检测 AUC/告警预算召回**不显著下降**（配对比检验） | CERT r4.2/r6.2 原始五源日志 + `answer/` 真值；会话聚合或可解释结构；单机 CPU/单卡；SHAP/注意力分解作对照 | **中**：会话级建模与 SHAP 已存在，须与「事后解释」形成「训练内可溯源」的清晰差异 | **高**：公开数据含事件级标签，单机可复现 |
| **RQ2（方向 2）** CERT 用户日志域的多源事件级联合溯源/攻击链重建：以用户风险分数为粗筛、事件排序为细筛，能否在 answer 场景真值上重建恶意事件序列？ | H2a：对 r4.2 三场景，溯源方法重建的 top-k 事件序列与 `answer/` 记录的恶意事件重叠度（事件级 recall@k / Jaccard）**显著高于**「纯风险分数 top-k 排序」这一朴素基线；H2b：联合五源（file+device+email+http+logon）优于单源 | CERT r4.2/r6.2 五源日志 + answer 文件；图/弱监督/序列溯源方法；单卡算力 | **中高**：provenance（MGDA/GETSUS）与 Ripple2Detect 已做攻击链/证据挖掘，须明确「用户级多源日志域」与「系统级 provenance」的设定差异，并警惕撞车 | **中高**：需先核实 `answer/` 文件的事件级粒度（是 user-day 还是事件级）——若仅 user-day 则 H2a 需重定义 ground truth |
| **RQ3（方向 3）** 跨版本泛化的失败模式归因与可复现评估协议：r4.2↔r6.2 迁移的性能下降可被分解为哪些因素，据此应如何设定切分/校准？ | H3a：跨版本退化主要来自**类别不平衡 + 特征/行为分布漂移**，而非模型容量；H3b：**校准（如 threshold calibration）+ 轻量领域适应**比「纯重训」更有效地恢复 r6.2 上的告警预算召回；H3c：在**时间切分 + 未见用户切分**协议下，跨版本结论与随机切分显著不同 | CERT r4.2/r5.2/r6.2；TRACE-IT 跨版本特征集（DOI 10.21227/bdg8-0565）；XGBoost/LightGBM 基线 + 校准；单机 CPU | **中**：XGBoost 研究已报告跨版本退化与阈值校准、DTITD 已用 LLM 迁移——须聚焦「可复现协议 + 失败模式归因」而非仅提新模型 | **高**：r4.2→r6.2 迁移实验公开数据可直接构造，算力要求最低 |

### 3.3 推荐问题与最小验证闭环

**推荐优先级：RQ3（跨版本泛化） > RQ1（事件级可溯源表征） > RQ2（多源事件级联合溯源）**。

理由：

1. **RQ3 的证据缺口最确凿、验证最便宜、算力最低**——系统综述（131 篇）明确指出版本偏倚与「无标准化协议」是领域级问题，而构造 r4.2→r6.2 迁移实验只需公开数据 + 轻量模型，且不要求回答 `answer/` 文件的事件级粒度（RQ2 的前置不确定性）。
2. RQ3 的产出是**可复现评估协议 + 失败模式归因**，天然衔接后续两个 case（method_differentiation 的路线论证、evaluation_design 的协议-指标-结论映射），是这条研究线的「地基」。
3. RQ1 是「方法新颖性」最强的方向，但需先有 RQ3 给出的「告警预算召回/校准」评估口径作为公平比较基线，否则易陷入「换个模型刷 AUC」的陷阱；RQ2 新颖性最高但撞车风险与 ground-truth 粒度不确定性也最高，宜在 RQ3 协议稳定后再做。

**最小验证闭环（RQ3，一周级/单机 CPU 可跑）**：

1. 用 TRACE-IT 派生特征（或自复现 Le 式会话特征）构造三组数据：r4.2、r6.2、以及 r4.2→r6.2 迁移。
2. 协议：**时间切分**（按 516 天前半/后半）+ **未见用户切分**（训练用户 ≠ 测试用户）各做一遍，与「随机切分」对照。
3. 模型：XGBoost/LightGBM + Isolation Forest 基线，报告 **ROC-AUC + 告警预算召回（如 top-100/1000）+ ECE/Brier 校准误差**，而非单一 F1。
4. 判决标准：能否复现「r6.2 默认阈值正例预测为空但 AUC 高」的校准失效现象，并验证「校准 + 领域适应 > 纯重训」这一假设 H3b。
5. 产出：一份「跨版本迁移协议 + 失败模式归因」的可复现报告，直接作为 case-3（evaluation_design）的输入。

## 4. 可支持的结论与建议

### 4.1 证据支持的结论

1. **CERT 是合成数据，任何结论都应写明「限于合成基准」**（Glasser & Lindauer 2013；综述均如此声明）。
2. **r4.2 密集 vs r6.2 稀疏的结构性差异是真实且被量化的**（70/1000 vs 5/4000），这是「跨版本泛化」问题的物理基础。
3. **「特征工程 > 模型复杂度」** 有强综述支撑（131 篇），意味着在有限算力下应优先投入特征/表征设计而非堆模型。
4. **黑箱模型的事件级不可溯源是公认缺口**，会话级/可分解表征与 SHAP 归因是两条已出现的缓解路径，但「训练内可溯源」仍是空白。
5. **跨版本泛化是证据最充分的缺口**：版本偏倚、无标准化切分、跨版本退化与校准失效均有直接证据（综述 + XGBoost 单篇 + TRACE-IT 的存在本身）。
6. **分析员真正需要的指标（告警预算召回、检测延迟、校准误差）长期被忽略**，是「面向分析员」这一约束下最可落地的研究增量。

### 4.2 适用前提与残余风险

- **口径不可比风险（高）**：各论文的切分协议、负采样比、指标定义不统一（综述明确点出），因此**本报告不主张跨研究硬比较绝对分数**；log2vec/CATE/结构偏差等具体 AUC/F1 数字来自二手汇总，**引用前须回原文核实**（已在 §2.2 标注「待核实」）。
- **合成性外推风险（高）**：CERT 场景脚本与角色模拟限制其到真实运营的迁移；本报告所有「缺口」都只在公开基准意义下成立。
- **r5.2 数字不一致（中）**：内鬼数/事件数不同来源冲突，需以 SEI 官方 readme 为准。
- **RQ2 的 ground-truth 粒度未确认（中）**：`answer/` 文件的标签粒度（user-day 还是事件级）直接决定 H2a 是否可测，是 RQ2 的第一前置验证项。
- **r7 存在性未确认（低）**：若存在更新版本，「跨版本」范围需相应更新。

### 4.3 下一步行动建议

1. **先冻结 RQ3**，跑 §3.3 的最小验证闭环（1 周、单机 CPU），产出可复现协议 + 失败模式归因。
2. **并行核实两件事**：① SEI 官方页确认 r5.2/r6.2 精确数字与是否存在 r7；② 下载 r4.2/r6.2 `answer/` 文件确认事件级粒度，据此判断 RQ2 的 ground truth 是否可测。
3. 用 RQ3 的评估口径（告警预算召回/校准/时间+未见用户切分）作为统一基线，再进入 case-2（method_differentiation）对 RQ1/RQ2 做路线差分论证，进入 case-3（evaluation_design）设计检测/溯源/泛化三主张的协议。

## 5. 待验证风险清单

| 风险/未知项 | 当前证据状态 | 影响 | 建议验证方式 |
|---|---|---|---|
| CERT 合成数据与真实企业日志的分布差异 | 已知且被多源承认（生成方法自述 + 综述局限） | 所有结论外推受限 | 迁移实验 + 局限声明；必要时用 TRACE-IT 的 10 用户真实实验室记录做定性对照 |
| r5.2 内鬼数/事件数口径不一（99 vs 未定；87.8M） | 单源冲突 | 影响「中间版本」迁移实验设计 | 以 SEI 官方 readme / KiltHub 文件清单为准 |
| 是否存在 r7 版本 | 本次检索未发现 | 「跨版本」范围界定 | SEI 官方库页 / KiltHub 复核 |
| log2vec / CATE / 结构偏差等具体 AUC-F1 数字 | 来自二手汇总，未核原文 | 引用可能失真 | 回读 CCS 2019 原文表、对应论文实验节 |
| r4.2→r6.2 跨版本退化的具体量级 | XGBoost 单篇摘要提及，未核原文表 | H3a 的定量起点不确定 | 回读 spy2.70122 原文实验表 |
| `answer/` 文件的事件级标签粒度 | 未确认（多数证据为 user-day 级） | RQ2 的 H2a 是否可测 | 下载并解析 answer/insiders.csv，确认字段粒度 |
| DTITD（LLM 迁移）可复现性 | 灰文献、无独立复现 | C9 证据强度弱 | 不采信为强证据；仅在路线论证中作「方向提示」 |
| 「特征工程 > 模型复杂度」的普适性 | 单一强综述（131 篇） | 或受版本/任务偏倚 | 在 RQ3 闭环中自行对照「同模型换特征」vs「同特征换模型」 |

## References

1. Glasser, J., & Lindauer, B. (2013). Bridging the Gap: A Pragmatic Approach to Generating Insider Threat Data. *2013 IEEE Security and Privacy Workshops (SPW)*, pp. 98–104. DOI: 10.1109/SPW.2013.37.
2. Software Engineering Institute, Carnegie Mellon University. (2016). *Insider Threat Test Dataset* (r1–r6.2). DOI: 10.1184/R1/12841247.v1. https://kilthub.cmu.edu/articles/dataset/Insider_Threat_Test_Dataset/12841247 （figshare 镜像：https://figshare.com/articles/dataset/Insider_Threat_Test_Dataset/12841247）
3. Tuor, A., Kaplan, S., Hutchinson, B., Nichols, N., & Robinson, S. (2017). Deep Learning for Unsupervised Insider Threat Detection in Structured Cybersecurity Data Streams. *AAAI AICS Workshop*. arXiv:1710.00811.
4. Liu, F., Wen, Y., Zhang, D., Jiang, X., Xing, X., & Meng, D. (2019). Log2vec: A Heterogeneous Graph Embedding Based Approach for Detecting Cyber Threats within Enterprise. *Proc. ACM CCS 2019*, pp. 1777–1794. DOI: 10.1145/3319535.3363224.
5. Yuan, S., Zheng, P., Wu, X., & Li, Q. (2019). Insider Threat Detection via Hierarchical Neural Temporal Point Processes. *2019 IEEE International Conference on Big Data*, pp. 1343–1350. DOI: 10.1109/BigData47090.2019.9005589. arXiv:1910.03171.
6. Le, D. C., & Zincir-Heywood, A. N. (2020). Analyzing Data Granularity Levels for Insider Threat Detection Using Machine Learning. *IEEE Transactions on Network and Service Management*, 17(1), 30–44.
7. Le, D. C., & Zincir-Heywood, A. N. (2019). Machine Learning Based Insider Threat Modelling and Detection. *IFIP/IEEE International Symposium on Integrated Network and Service Management (IM)*.
8. Le, D. C., Zincir-Heywood, A. N., & Heywood, M. I. (2019). Dynamic Insider Threat Detection Based on Adaptable Genetic Programming. *2019 IEEE Conference on Communications and Network Security (CNS) — CISDA Workshop*.
9. Homoliak, I., Toffalini, F., Guarnizo, J., Elovici, Y., & Ochoa, M. (2019). Insight into Insiders and IT: A Survey of Insider Threat Taxonomy, Analysis, Modeling, and Countermeasures. *ACM Computing Surveys*, 52(2).
10. Jurišić, M., & Tomičić, I. (2026). The Cert Dataset Decade: A Systematic Review of Methodological Evolution and Performance Bias. *Varstvoslovje (Journal of Criminal Justice and Security)*, 28, 1–24. https://www.fvv.um.si/rV/arhiv/2026/2026-02-Jurisic-Tomicic-E.html
11. TRACE-IT: Real World & CERT Insider Threat Data. IEEE DataPort. DOI: 10.21227/bdg8-0565. https://ieee-dataport.org/documents/trace-it-real-world-cert-insider-threat-data
12. (Bayesian-optimized XGBoost cross-version study) An Efficient Insider Threat Detection Framework Using Bayesian-Optimized XGBoost. *Security and Privacy*. DOI: 10.1002/spy2.70122. （跨版本退化 + r6.2 校准失效）
13. Ripple2Detect: A semantic similarity learning based framework for insider threat multi-step evidence detection. *Computers & Security*. DOI: 10.1016/S0167404825000768.
14. MGDA: A provenance graph-based framework for threat detection and attack scenario reconstruction. *Computer Networks*. DOI: 10.1016/S1389128625007728. （另见 GETSUS / TransProv provenance 类工作）
15. (Digital Twin + Transformer + Transfer Learning, DTITD / FedITD) University of Ottawa PhD thesis. https://ruor.uottawa.ca/items/1e5f631b-bdac-4b12-b327-80f31beb59ba （灰文献，证据强度弱）

---

> **口径归一说明（对应工作流 Step 4）**：本报告未做跨研究的绝对分数比较——各来源在切分协议、负采样比、指标定义（AUC vs F1 vs DR）上口径不统一，属 `incompatible`。凡涉及量化数字，均以「来源定位 + 单源/多源」标注，二手汇总数字显式标「待核实」。可做 `compatible` 比较的仅限同一研究内部（如 XGBoost 研究的 r4.2/r5.2/r6.2 同口径对比）。

---

## 审查评注（Claude 复核，2026-09-29）

> 本节为审查评注，非报告正文；记录引用核验结果与待修问题，作为后续系统调试的对照。

### ✅ 已核实无误

- [Jurišić & Tomičić 2026 综述](https://www.fvv.um.si/rV/arhiv/2026/2026-02-Jurisic-Tomicic-E.html)（131 篇）真实存在，关键结论吻合。
- [TRACE-IT](https://ieee-dataport.org/documents/trace-it-real-world-cert-insider-threat-data)（DOI 10.21227/bdg8-0565）真实存在。
- [Ripple2Detect](https://www.sciencedirect.com/science/article/abs/pii/S0167404825000768)（Computers & Security 2025）真实存在。
- Bayesian-Optimized XGBoost（DOI 10.1002/spy2.70122）论文本身真实存在。

### ⚠️ 发现的问题

1. **「r6.2 默认阈值 F1=0.0000 / AUC 0.9725 / 校准失效 / 跨版本退化」归错了论文**。
   - 该结论实际来自一篇 **XGBoost + Grid Search + SMOTE** 的研究（如 UPN Veteran Yogyakarta），而非本报告引用的 **Bayesian-Optimized XGBoost（spy2.70122）**；后者声称的是跨版本**鲁棒/可迁移**，与「校准失效」方向相反。
   - 影响：§2.2 #19、C8、风险清单第 5 行的引用指向有误。
   - 建议：将该结论的引用改为 Grid Search + SMOTE 那篇，或显式标注「来源论文待确认」。
