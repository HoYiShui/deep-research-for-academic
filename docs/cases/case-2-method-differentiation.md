# Case 2: method_differentiation — 多源日志内部威胁检测与事件级溯源的四路线差分

> 任务类型：method_differentiation（方法差分）
> 用途：端到端 gold 报告，作为后续 Agent 系统调试的 diff 基准。
> 生成方式：真实检索（web search + 读原文），逐条可回溯，未确证处显式标注「待核实」。

---

## ① 原始 Query

```text
针对"多源日志下的内部威胁检测与事件级溯源"，比较行为特征建模、时序建模、图建模和
弱监督溯源路线，分析它们各自解决的输入、输出和训练问题，并判断我的方案应如何与
最相近工作形成可验证差异。
```

---

## ② ResearchBrief（10 字段，冻结）

| 字段 | 取值 |
|---|---|
| task_type | method_differentiation |
| decision_goal | 判断「多源日志下的内部威胁检测 + 事件级溯源」方案，应如何与最近邻工作形成**可验证差异**（即差异化主张 + 贡献边界） |
| research_object | 多源日志（logon/device/file/email/http）下的内部威胁检测，输出 = 用户风险分数 + 事件级排序/定位；面向分析员 |
| scope | 公开 CERT 数据（r4.2/r5.2/r6.2）、有限算力（单机/单卡） |
| comparison_scope | 四条路线：① 行为特征建模（特征工程 + 树/浅层模型）；② 时序建模（RNN/LSTM/Transformer/时序点过程）；③ 图建模（异构图嵌入/GNN）；④ 弱监督溯源（MIL/PU/证据挖掘/provenance） |
| claims_to_verify | 每条路线各自解决的**输入、输出、训练**问题；用户方案与每条路线最近邻工作的差异点是否可验证 |
| evidence_requirements | 各路线代表性论文（含输入/输出/训练设定与结果）；系统综述佐证 |
| conclusion_boundary | 结论止于「差分主张 + 贡献边界 + 相似性风险」粒度；不给出具体模型实现或超参 |
| deliverable | 候选路线机制说明 + 最近邻比较矩阵 + 可验证差分研究论断 + 贡献边界与相似性风险 |
| assumptions | ① 用户方案同时输出「用户风险分数」与「事件排序」（承接 case-1 RQ1/RQ2 的定位）；② 公开数据 + 有限算力；③ 「可验证差异」= 能在公开基准上被实验证实/证伪，而非定性宣称 |

---

# 多源日志内部威胁检测与事件级溯源：四条建模路线的机制与差分论证

## 0. Research Brief

- **研究类型**：method_differentiation
- **要解决的决策**：四条候选路线（行为特征 / 时序 / 图 / 弱监督溯源）各自解决什么输入、输出、训练问题；用户方案（多源检测 + 事件级溯源）应与最近邻工作形成哪些**可验证**差异
- **研究对象与范围**：公开 CERT 多源日志；输出 = 用户风险分数 + 事件排序；有限算力
- **结论边界**：止于差分主张与贡献边界；不给实现、不承诺生产效果
- **关键假设**：用户方案为「检测 + 溯源」双输出；公开数据可复现

## 1. 问题定义与研究边界

### 1.1 目标问题

内部威胁检测在方法上已分化出四条可辨识的路线，但它们解决的是**不同层面**的问题：行为特征建模解决「表征可解释、训练便宜」，时序建模解决「行为演化/长程依赖」，图建模解决「多实体关系」，弱监督溯源解决「标签稀缺下的事件定位」。用户方案同时要「检测」（用户级风险分数）和「溯源」（事件级排序），因此**关键决策不是选哪条路线，而是：以哪条路线为主干，从哪些最近邻工作上建立可证伪的差异**。

### 1.2 系统、数据与威胁边界

同 case-1 §1.2：CERT 五源日志（logon/device/file/email/http）+ LDAP 组织元数据 + psychometric 人格；r4.2（1000 用户/70 内鬼/密集）、r6.2（4000 用户/5 内鬼/稀疏）、r5.2（2000 用户/4 场景/中间）。威胁类型 = 数据窃取、知识产权盗取、IT 破坏、（r5.2+）横向移动持续窃取。标签默认 **user-day** 级（事件级真值仅在 `answer/` 文件以场景描述 + 文件/时间窗形式部分存在，粒度待核实）。

### 1.3 本报告不回答的问题

- 不给具体模型架构、损失函数、超参。
- 不对四条路线做「谁绝对更优」的跨研究硬排序（口径不一致，见 §2.3 / §4.2）。
- 不回答「弱监督溯源」在系统级 provenance（非用户日志域）上的工程实现细节。

## 2. 证据基础与关键发现

### 2.1 检索范围与信源标准

- 信源分级同 case-1：同行评议论文（含 DOI/页码/arXiv ID）> 系统综述 > 数据集文档 > 灰文献（标注）。
- 检索子问题：四路线各自代表性工作 + 输入/输出/训练设定 + 与「检测+溯源双输出」的最近邻关系。

### 2.2 已确认事实（含来源定位）

**路线 1：行为特征建模**

1. **会话级手工特征 + 树/浅层模型是基线范式**：Le & Zincir-Heywood（TNSM 17(1):30–44）用会话聚合特征（r4.2 = 107 维、r5.2 = 190 维），分「活动特征 + 上下文特征」；后续会话类研究用 35/36 维会话向量（含会话时序、事件计数、外部邮件、大文件传输、不可信域交互等）。
2. **监督树模型显著优于无监督浅层模型**：一项会话级基准在 r4.2 上报告 XGBoost F1=82.25%/AUC=99.88%、DT F1=80.31%、RF F1=75.47%；Isolation Forest / K-Means 的 Precision < 0.5%（SSRN/ScienceDirect 会话研究）。另一 ACM 基线研究给出 One-Class SVM F1=0.0051、Isolation Forest F1=0.0110（极高误报、精度崩溃）。
3. **训练问题**：需要 **user-day 级标注**做监督训练；无监督变体（IF/OCSVM）在极端不平衡下精度崩坏。

**路线 2：时序建模**

4. **RNN/LSTM**：Tuor et al.（arXiv:1710.00811）per-user 在线无监督 DNN/RNN，24h 窗口聚合 408 特征，按预测误差打分并**把异常分数分解到单个行为特征**（早期「可解释分数」尝试），用 CERT v6.2。
5. **Transformer/自注意力**：DTITD/DistilledTrans 用 BERT/RoBERTa/GPT-2 做迁移与检测（r6.2 上 DistilledTrans 更优、r4.2 上微调 BERT 更优）；UBS（arXiv:2506.23446）仅用良性数据训练 Transformer encoder + 重建误差 + LOF/OCSVM/IF 打分；CATE（CNN attention + Transformer encoder，二手来源称 r4.2 F1 96.18%，**待核实**）；编码器-解码器交叉注意力类工作。自注意力被认为能捕获 RNN/LSTM 难及的**长程依赖**且可并行。
6. **时序点过程**：Yuan et al.（IEEE BigData 2019, pp.1343–1350）层级神经时序点过程（seq2seq + marked TPP），捕获「活动类型 + 多尺度时间」。
7. **训练问题**：多为 **per-user 自监督/重建**（无需攻击样本）或序列监督；**输出是用户/序列级分数，事件级定位只能靠注意力权重做弱代理**。

**路线 3：图建模**

8. **异构图嵌入**：Log2vec（CCS 2019, pp.1777–1794）把用户日志构造成异构图，随机游走 + word2vec + 聚类阈值检测，**无需攻击样本**、天然适合不平衡。
9. **GNN/HGNN**：GCN+Bi-LSTM（显式/隐式图 + 多头注意力 + Bi-LSTM）在 r5.2 报告 AUC=98.62/DR=100%/FPR=0.05、r6.2 AUC=88.48/DR=80.15%/FPR=0.15；Log2Graph（Fei et al., JCS 2025, DOI 10.3233/JCS-230092）AUC 0.986（CERT）/0.997（LANL）；ATHITD（Computers & Security）时空异构图注意力；DH-GNN（动态异构图 + 对比异常学习）。ITDE 用两级注意力 + 元路径。
10. **训练问题**：多为**无监督/自监督**（对比、重建、结构偏差）；输出节点/子图异常分数。**局限**：计算成本高、可解释性差（128–256 维 embedding 不可回溯到事件）。

**路线 4：弱监督溯源**

11. **MIL + 弱监督定位（最近邻）**：RMSL（arXiv:2508.11472）把 ITD **首次**形式化为 MIL：只有序列级标签（正常/异常），要输出**行为级异常分数**做定位；三阶段（零正样本超球 warm-up → MIL 弱监督 → 自适应去偏自训练），在 r4.2/r5.2 上比 16 个基线 AUC 提升 9.78%/3.98%。
12. **活动级实时检测**：LAN（Learning Adaptive Neighbors）做**活动级**（非用户/时段级）实时检测，图结构学习 + 自监督/监督混合损失，r4.2/r5.2 比 9 基线 AUC 提升 ≥9.92%/6.35%。
13. **MIL + PU 学习**：IEEE 工作用 PU 损失 + 加权 noisy-OR 袋损失同时预测实例级与袋级类别（通用异常检测，非 ITD 专属）。
14. **证据挖掘/provenance**：Ripple2Detect（Computers & Security）建证据库 + 知识图谱，BERT 对比学习做多步证据挖掘；MGDA/GETSUS/TransProv 在 provenance 图/日志上做攻击链重建与溯源（多为系统级 provenance 或非 CERT 用户日志域）。
15. **训练问题**：**只需序列级/袋级弱标签**，缓解事件级标注稀缺；输出行为/事件级分数。**局限**：MIL 选择偏倚、弱标签噪声、尚未与「跨版本泛化」结合验证。

**跨路线的领域级事实**

16. **特征工程 > 模型复杂度**（131 篇系统综述，Jurišić & Tomičić 2026）——意味着差分主张应优先落在「表征/训练设定/评估协议」而非「换更深模型」。
17. **黑箱模型事件级不可溯**是路线 2/3 共同未解决问题，而路线 4 恰好以「事件级定位」为核心输出——这是用户方案与最近邻形成差异的主战场。

### 2.3 研究论断与证据缺口（Claim ↔ 证据关系）

| 论断 | 证据定位 | 关系 |
|---|---|---|
| C1「行为特征建模可解释且便宜，但依赖 user-day 标签、无监督精度崩坏」 | TNSM 17(1)；XGBoost/OCSVM/IF 基准 | **supports** |
| C2「时序建模捕获长程依赖，但输出黑箱、事件定位弱」 | Tuor 2017（分解是个例）；Transformer 综述式结论 | **supports**（事件定位弱为工作自述/综述，非独立量化） |
| C3「图建模捕获关系结构、可无监督，但计算高、不可解释」 | Log2vec CCS 2019；GCN+Bi-LSTM；Log2Graph | **supports** |
| C4「弱监督溯源可直接输出事件/行为级定位，是最贴近『溯源』的路线」 | RMSL（MIL 定位）；LAN（活动级） | **supports** |
| C5「检测与溯源双输出、且跨版本泛化的统一框架仍是缺口」 | RMSL 只做定位未做用户级检测对比 + 未做跨版本；综述指无标准化协议 | **partial**（缺口是「合成」推断，多源可佐证但无单一直接证据） |
| C6「特征工程比模型复杂度更决定性能」 | Jurišić & Tomičić 2026（131 篇） | **supports** |

**证据缺口**：缺「用户级检测 + 事件级溯源」**联合优化且在同一协议下量化**的工作；缺弱监督溯源路线的**跨版本泛化**证据。

## 3. 技术路线与方法差分论证

### 3.1 候选路线及机制

| 路线 | 核心机制 | 输入 | 输出 | 训练问题（解决什么） |
|---|---|---|---|---|
| ① 行为特征建模 | 会话/日聚合手工特征 + 树/浅层分类 | 会话级特征向量 | 用户/会话风险分数 | 解决「表征可解释 + 训练便宜 + 有标注时的强基线」；**不解决**时序依赖、多实体关系、事件定位、无监督精度 |
| ② 时序建模 | RNN/LSTM/Transformer/TPP 建模行为序列 | 事件/特征序列 | 序列/用户异常分数 | 解决「行为演化与长程依赖 + 未知威胁（自监督重建）」；**不解决**事件级可解释定位、关系结构 |
| ③ 图建模 | 异构图构建 + 图嵌入/GNN 消息传递 | 用户-设备-文件-URL 异构图 | 节点/子图异常分数 | 解决「多实体关系 + 无攻击样本训练」；**不解决**计算成本、事件级可解释 |
| ④ 弱监督溯源 | MIL/PU/证据挖掘，序列级标签 → 事件级分数 | 用户行为序列（袋）+ 弱标签 | 行为/事件级异常分数（定位） | 解决「标签稀缺 + 事件级定位」；**不解决**（尚未充分）用户级检测联合、跨版本泛化 |

### 3.2 最近邻工作比较矩阵

| 工作/路线 | 输入与表示 | 核心机制 | 输出 | 解决的限制 | 未解决问题 |
|---|---|---|---|---|---|
| Le & Zincir-Heywood（TNSM 2020）— 行为特征 | 会话聚合 107/190 维特征 | 手工特征 + 监督/无监督 ML | 用户级风险 | 可解释、便宜、可复现 | 依赖 user-day 标签；无时序/关系/事件定位 |
| Tuor et al. 2017 — 时序(早期) | 24h 窗口 408 特征序列 | per-user RNN 重建误差 + 分数分解 | 用户级分数（可分解） | 在线无监督、分数可分解 | 分解是特征级非事件级；v6.2 单版本 |
| Yuan et al. 2019 — 时序点过程 | 活动类型 + 时间戳 | seq2seq + marked TPP | 用户异常分数 | 显式建模多尺度时间 | 无事件级定位、无跨版本 |
| UBS / DTITD / CATE — Transformer | 用户序列 token 化 | 自注意力 + 重建/分类 | 用户/序列分数 | 长程依赖、可并行 | 黑箱、事件定位弱、算力高 |
| Log2vec（CCS 2019）— 图 | 用户日志异构图 | 随机游走+word2vec+聚类 | 事件簇异常 | 无需攻击样本、捕获关系 | 未与用户级检测统一、迁移未知 |
| GCN+Bi-LSTM / Log2Graph / ATHITD — GNN | 显式/隐式图 + 时序 | 图卷积 + 注意力 + Bi-LSTM | 节点/用户分数 | 结构+时序联合、强 AUC | 高算力、embedding 不可回溯 |
| **RMSL（arXiv 2508.11472）— 弱监督溯源【最近邻】** | 用户行为序列（袋）+ 序列级弱标签 | MIL + 多超球 + 去偏自训练 | **行为级异常分数（定位）** | 无需事件级标签即可定位 | 未联合用户级检测对比、未做跨版本 |
| LAN — 弱监督/活动级 | 活动序列 + 图结构 | 图结构学习 + 混合损失 | 活动级分数（实时） | 活动粒度定位 | 未做跨版本、计算较高 |
| Ripple2Detect / MGDA — 证据/溯源 | 操作序列 / provenance 图 | 证据挖掘/图重建 | 事件序列/证据 | 多步证据链 | 多为系统级 provenance 或非 CERT 域 |

### 3.3 可验证差分研究论断

用户方案（多源日志检测 + 事件级溯源，双输出：用户风险分数 + 事件排序）应把差异化主张**写成可证伪假设**，而非定性「更全面」。建议四条可验证差分论断：

- **D1（vs 行为特征 ①）**：在**同等告警预算**下，用户方案的用户级检测（告警预算召回 / PR-AUC）不显著低于 XGBoost 特征基线，但**事件级定位命中（hit@k）显著高于**该基线（后者无事件输出 → hit@k≈0）。**可证伪**：若连 XGBoost 的检测都打不过，D1 失败。
- **D2（vs 时序 ②）**：相比黑箱 Transformer/LSTM（事件定位只能靠注意力权重），用户方案的事件排序在 answer 真值上的事件级 recall@k **显著更高**，且**检测性能不显著下降**。**可证伪**：若注意力基线的事件定位经调优后追平，D2 弱化。
- **D3（vs 图 ③）**：相比 GNN（Log2Graph/GCN+Bi-LSTM），用户方案在**有限算力**（单卡/CPU）下达到同量级检测，且事件排序可回溯；图方法 embedding 不可回溯。**可证伪**：以「每 F1 点的训练/推理算力」为公平口径对比。
- **D4（vs 弱监督溯源 ④，最关键的差异化）**：RMSL 只输出行为级定位、未统一用户级检测、未做跨版本。用户方案应主张：**用同一表示同时产出「用户风险分数」与「事件排序」，并在跨版本（r4.2→r6.2）迁移下保持事件定位能力**——这是 RMSL/LAN 均未覆盖的。**可证伪**：跨版本迁移实验 + 事件定位指标（承接 case-3 协议）。

**差分本质**：四条路线分别解决「可解释 / 时序 / 关系 / 定位」中的某一维，用户方案的可验证增量不是「全部都要」，而是**「检测 + 溯源双输出的统一 + 跨版本泛化」**这两点——它们各自在现有最近邻中都是空白，且都能在公开数据上被实验证实或证伪。

### 3.4 贡献边界与相似性风险

- **贡献边界**：本方案的合理贡献声明应为「**统一的检测-溯源双输出 + 跨版本可泛化的事件定位**」，而非「新架构刷新 AUC」。避免与「堆深模型」的军备竞赛（综述已证特征/协议 > 模型复杂度）。
- **相似性风险（高）——RMSL**：若用户方案的事件定位靠 MIL/弱监督，则与 RMSL（arXiv:2508.11472）高度重叠。**规避**：① 明确增量是「用户级检测与事件级定位的**联合/统一** + 跨版本」，而非「又一个 MIL 定位器」；② 在实验上直接与 RMSL 对标（r4.2/r5.2 同协议）。
- **相似性风险（中高）——provenance/证据挖掘**：Ripple2Detect/MGDA 已做事件/证据链，需声明「用户日志域（CERT 五源）」vs「系统级 provenance」的设定差异，否则会被审稿人视为旧瓶新酒。
- **相似性风险（中）——可解释分数分解**：Tuor 2017 已做「分数分解」，用户方案的「事件排序」须区别于「特征级分解」（粒度：事件 vs 特征）。
- **相似性风险（中）——Transformer/图黑箱**：若用注意力/图做主干，需回答「事件排序从何而来」，否则落入 D2/D3 已指出的事件不可溯。

## 4. 可支持的结论与建议

### 4.1 证据支持的结论

1. 四条路线**不是竞争替代关系，而是解决不同维度**：行为特征=可解释/便宜、时序=演化/长程、图=关系/无监督、弱监督溯源=标签稀缺下的定位。
2. 用户方案「检测 + 溯源」双输出**最接近的最近邻是弱监督溯源（RMSL/LAN）**，其次是可解释分数分解（Tuor）与会话特征（Le）。
3. **「统一的检测-溯源双输出 + 跨版本泛化」是当前有证据支持、且可验证的空白**：RMSL 未联合用户级检测、未做跨版本；综述确认无标准化协议。
4. 差异化主张应落在**表征/训练设定/评估协议**，而非模型深度（特征工程 > 模型复杂度，131 篇综述）。

### 4.2 适用前提与残余风险

- 所有差分主张**依赖事件级 ground truth 可用**（`answer/` 文件粒度待核实，同 case-1 RQ2 前置项）；若仅有 user-day 标签，D2/D4 的事件级 hit@k/recall@k 需改为「攻击窗口内命中」的代理指标。
- 具体分数（CATE F1 96.18%、GCN+Bi-LSTM AUC 98.62/88.48 等）来自二手汇总，**引用前须回原文核实**。
- 跨版本泛化差分（D4）的证据是「领域缺口 + 单篇迁移研究」，非多独立复现，属 `partial`。

### 4.3 下一步行动建议

1. 以 **D4 为最高优先差分主张**（联合双输出 + 跨版本），D1/D2/D3 为消融对照。
2. 先核实 `answer/` 事件级粒度（决定事件定位指标的可行性），再锁定 RMSL/LAN/Tuor/Le/XGBoost 为必比基线。
3. 进入 case-3：把 D1–D4 映射到可执行的实验协议与指标。

## 5. 待验证风险清单

| 风险/未知项 | 当前证据状态 | 影响 | 建议验证方式 |
|---|---|---|---|
| RMSL 撞车风险（MIL 弱监督定位） | RMSL 已存在（arXiv:2508.11472） | 贡献被稀释 | 明确「检测+溯源联合 + 跨版本」增量，与 RMSL 同协议对标 |
| provenance/证据挖掘设定重叠 | Ripple2Detect/MGDA 已做事件链 | 被审为旧瓶新酒 | 声明「CERT 用户日志域 vs 系统级 provenance」差异 |
| `answer/` 事件级标签粒度未确认 | 多数证据为 user-day 级 | D2/D4 的事件指标不可直接测 | 下载解析 answer/insiders.csv，确认字段粒度 |
| CATE/GCN 具体分数未核原文 | 二手汇总 | 引用失真 | 回读原文实验表 |
| 跨版本泛化差分证据强度 | 单篇迁移 + 综述缺口（partial） | D4 起点不硬 | 用 case-3 协议自行复现迁移退化 |
| 「特征工程 > 模型复杂度」普适性 | 单一强综述 | 差分主张或受影响 | 在方案内做「换特征 vs 换模型」对照 |

## References

1. Le, D. C., & Zincir-Heywood, A. N. (2020). Analyzing Data Granularity Levels for Insider Threat Detection Using Machine Learning. *IEEE TNSM*, 17(1), 30–44.
2. Tuor, A., Kaplan, S., Hutchinson, B., Nichols, N., & Robinson, S. (2017). Deep Learning for Unsupervised Insider Threat Detection in Structured Cybersecurity Data Streams. *AAAI AICS Workshop*. arXiv:1710.00811.
3. Yuan, S., Zheng, P., Wu, X., & Li, Q. (2019). Insider Threat Detection via Hierarchical Neural Temporal Point Processes. *IEEE BigData 2019*, 1343–1350. DOI: 10.1109/BigData47090.2019.9005589.
4. Liu, F., Wen, Y., Zhang, D., Jiang, X., Xing, X., & Meng, D. (2019). Log2vec: A Heterogeneous Graph Embedding Based Approach for Detecting Cyber Threats within Enterprise. *ACM CCS 2019*, 1777–1794. DOI: 10.1145/3319535.3363224.
5. Fei, K., Zhou, J., Su, L., Wang, W., & Chen, Y. (2025). Log2Graph: A graph convolution neural network based method for insider threat detection. *Journal of Computer Security*. DOI: 10.3233/JCS-230092.
6. (GCN + Bi-LSTM explicit/implicit graph) Insider Threat Detection Using GCN and Bi-LSTM with Explicit and Implicit Graph Representations. Northumbria University（含 PhD thesis 全文，r5.2/r6.2 结果）。
7. (ATHITD) Attention-based temporal heterogeneous graph neural network for insider threat detection. *Computers & Security*. DOI: 10.1016/S0167404825002767.
8. (RMSL) Weakly-Supervised Insider Threat Detection with Robust Multi-sphere Learning. arXiv:2508.11472.
9. (LAN) Learning Adaptive Neighbors for Real-Time Insider Threat Detection.（活动级实时检测；venue 待核实）
10. (MIL + instance-level PU) Multiple Instance Learning With Instance-Level Positive-Unlabeled Learning in Anomaly Detection. IEEE（通用异常检测）。
11. (UBS Transformer) Enhancing Insider Threat Detection Using User-Based Sequencing and Transformer Encoders. arXiv:2506.23446.
12. (DTITD/DistilledTrans) An Intelligent Insider Threat Detection Framework Based on Digital Twin and Self-Attention Based Deep Learning Models. IEEE Access（DOI: 10.1109/…10285086）。
13. (Ripple2Detect) A semantic similarity learning based framework for insider threat multi-step evidence detection. *Computers & Security*. DOI: 10.1016/S0167404825000768.
14. (MGDA) A provenance graph-based framework for threat detection and attack scenario reconstruction. *Computer Networks*. DOI: 10.1016/S1389128625007728.
15. Jurišić, M., & Tomičić, I. (2026). The Cert Dataset Decade: A Systematic Review of Methodological Evolution and Performance Bias. *Varstvoslovje*, 28, 1–24.

---

> **口径归一说明（对应工作流 Step 4）**：跨路线的绝对分数比较属 `incompatible`（切分/指标/版本不统一），本报告只在**同一研究内部**引用数字（如 GCN+Bi-LSTM 的 r5.2 vs r6.2、RMSL 相对 16 基线的提升）。差分主张 D1–D4 设计为「可证伪假设」，其验证依赖 case-3 给出的统一协议，而非本报告的绝对分数。

---

## 审查评注（Claude 复核，2026-09-29）

> 本节为审查评注，非报告正文；记录引用核验结果与待修问题，作为后续系统调试的对照。

### ✅ 已核实无误

- [RMSL](https://browse-export.arxiv.org/pdf/2508.11472)（arXiv:2508.11472）真实存在，作者 / 三阶段训练（多超球 warm-up → MIL → 去偏自训练）/ 相对 16 基线 9.78%·3.98% 提升等细节吻合。
- [Log2Graph](https://journals.sagepub.com/doi/pdf/10.3233/JCS-230092)（JCS 2025, DOI 10.3233/JCS-230092）真实存在，AUC 0.986(CERT)/0.997(LANL) 吻合。

### ⚠️ 备注（非新问题）

- 报告已**自行诚实标注**的待核实项——LAN 的 venue「待核实」、CATE F1 96.18%、GCN+Bi-LSTM AUC 98.62 等「二手汇总」数字——属于正确的诚实标注，无需额外修正。
- 未发现新的引用归因错误。本 case 整体质量高，唯一较弱锚点是 LAN（venue 待核实），报告已自标。
