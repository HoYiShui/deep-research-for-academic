# T026部分进展：正文下载安全边界

日期：2026-10-06。**T026保持未完成**。下载阶段和后续HTML内容闭环的证据分别记录如下；不会用搜索摘要、假正文、假页码填补PDF Parser能力，也尚不产生业务 Evidence。

## 已实现

`backend/infrastructure/fetch/http.py` 提供内部 RestrictedDownloader，结果是 DownloadedBody：实际下载字节、最终URL、MIME、字节SHA-256、响应ETag/Last-Modified、重定向链。响应头不是论文版本的充分证明，PDF签名也不是有效PDF或已解析页码的证明。

- URL仅HTTP(S)，拒绝凭据、控制字符、反斜杠、非法端口、localhost/.local/.internal、非公网字面IP；Unicode路径/查询编码为ASCII网络URL，fragment不制造新的正文身份。
- 在**真实连接边界**解析DNS；全部答案必须为公网，混合公私答案也拒绝；只将已校验的数字IP传给TCP连接器，不再用原域名二次解析。不对失败IP做隐式网络重试。
- TLS的server_hostname与HTTP Host保持原域名，默认SSL上下文验证证书和hostname；不为IP固定关闭TLS验证。
- 每个重定向使用新连接池，因此即使同域跳转也重新解析/校验，最多3次重定向；循环、缺Location、私网跳转失败。不携带环境HTTP_PROXY、认证头或cookie。
- 30秒总deadline覆盖DNS/连接/响应/跳转；原文50MiB硬上限同时检查声明长度和流式累计长度；成功空body也失败；非200、未知MIME、错误PDF签名、PDF伪装text均失败。
- 请求identity编码，拒绝压缩响应，不对不可信压缩内容做无界解压。PDF/HTML均不执行；下载结果还不具备解析位置。
- 显式锁定公开API依赖 `httpcore[asyncio]==1.0.9`，使用其network_backend接口；未改其私有连接池或关闭安全保护。[官方接口说明](https://www.encode.io/httpcore/network-backends/)
- 旧 `infrastructure/parser/pdf.py::HttpFetch` 仅作文本兼容桥，复用同一Downloader，论文/PDF明确 `fetch_parser_required`，不将PDF二进制当正文字符串。旧MinerU占位改为 `parser_not_configured`，不再返回假成功的空正文。真实版本化Parser仍是T043工作。

## 验证与实际环境

```bash
# backend目录
uv run pytest -q tests/contract/test_fetch.py tests/contract/test_content.py
uv run python -m scripts.verify_fetch_download --json
```

最终定向 **60 passed in 0.21s**。测试走实际httpcore HTTP栈与受控socket回放，而非替换Downloader方法；证明私网/metadata/保留地址/IPv6映射/转换地址拦截、数字IP固定、TLS hostname及验证配置、HTTP Host、同域DNS重绑定、相对跳转与3跳上限、大小限制、原始hash、异常脱敏、DNS deadline和父任务取消。真实TLS握手/证书链与真实PDF解析不由这些回放测试证明。

最终版本在独立验证PG及唯一测试库/MinIO桶完成全量 **714 passed in 81.91s**；Ruff、format检查和 `git diff --check` 通过。原PG仍未修复，此处不是它的恢复证明。

额外用本机getaddrinfo验证十进制IP、短IPv4、十六进制IPv4会落到loopback并被拒绝，不把异常拼写当公网域名放行。

联网目标：`https://arxiv.org/pdf/1706.03762v7`，探针结果：

```json
{"scope":"raw_download_only","parsed":false,"stored":false,"status":"failed","code":"fetch_url_forbidden","retryable":false,"elapsed_seconds":0.021}
```

只读检查证实系统DNS返回：`arxiv.org → 198.18.0.91`、`www.python-httpx.org → 198.18.0.92`，均非公网。可能来自代理Fake-IP，但没有把推测写成已确认的代理配置。**不因此放行198.18/15或关闭防SSRF**，已向用户询问真实DNS配置；未修改系统/代理设置。

## 剩余验收

- PDF对应的受控解析版本、内容引用、真实位置；HTML实现见下节。
- 真实PDF经过本地Parser产生非空正文/表格/页码；可信摘录范围、hash和Source身份由T027连接验证。
- 正式PhaseTools/ToolCallService接入，不将此无Run的单次只读探针冒充预算/账本闭环。
- 在真实公网DNS环境再次下载论文并验证真实来源。当前未宣称论文取证通过。

未触碰已损坏的原PG数据或用户对象、未改backend/.env、未执行参考原型。

## 后续进展：HTML与共享内容存储

实现路径：`application/ports.py` 的 ContentStorePort/DocumentParserPort；`domain/documents.py` 的严格输入输出；`infrastructure/storage/content.py`、`infrastructure/parser/html.py`、`infrastructure/fetch/document.py`。原工具缓存与新内容存储复用 `content_cache.py::MinioObjectIO` 的有界线程I/O，不改变工具缓存的10MiB契约；文档内容上限50MiB。

- 原文与解析JSON均按实际字节SHA-256落不可变对象，key限定在Run或KB/DocumentVersion内。同key异字节在S3调用前拒绝，已有对象损坏不自动覆盖修复；读取重验大小与hash。删除只允许精确资源key/prefix，不允许根前缀。业务归属授权与清理生命周期仍由App负责，未冒充已经实现KB管理。
- HTML/text真正解析已保存原文，固定 `dr4a-html-v1`，按原始文件行号定位，不把实体解码新增换行当来源行号。标题上下文、完整原子HTML表格、MathML、上下标与代码缩进保留；不把表格猜成数值网格，不制造PDF页码。
- 空内容、错误编码、未闭合原子块、超字节/块/单块限制失败，不静默截断。解析在线程执行；单槽覆盖原文读取与解析，排队任务不提前读取，读阶段取消释放槽，运行中的解析取消不提前释放执行容量。
- `HTTPDocumentFetch.fetch(SearchResult)` 返回原文/解析内容引用及位置；论文必须访问明确的PDF fulltext目标，不退回摘要。`read_parsed` 不重新联网或解析，验证Run范围、JSON/hash/版本/位置与原文仍存在且完整。HTML Parser收到PDF明确失败，只可能留下原文对象，不能伪造解析成功。

验证命令（backend目录）：

```bash
uv run pytest -q --tb=short tests/contract/test_document_parser.py tests/contract/test_document_content.py tests/unit/test_content_cache.py tests/integration/test_mono_document_content.py tests/integration/test_mono_fetched_document.py
```

定向 **54 passed in 0.71s**；独立验证PG + 真实MinIO唯一测试资源的全量 **757 passed in 83.73s**，Ruff/format/diff检查通过。真实MinIO验证包含并发去重、重开Store回读、损坏检测、限定版本删除不影响邻居及11MiB对象（超过工具缓存限额）。Fetch集成使用真实httpcore协议栈的**受控socket回放**、真正HTML解析和真正MinIO，不是公网网页/PDF成功证据。关闭/重开存储后回读不触发重新下载；删除原文后不能单凭解析JSON继续使用。

当前仍缺：MinerU真实PDF结构化解析、正式Run工具账本与候选授权绑定、Scout来源/Evidence回链以及真实公网论文验收。T026/T043不能勾选。准备MinerU时本机对PyPI的urllib与curl请求均出现TLS连接中断；未关闭证书验证或改变系统代理。

## Scout原文与coverage边界（T023/T027部分）

新增 `domain/research/agents/originals.py` 与 `coverage.py`，旧 `scout.research` 明确标为legacy，不将其搜索snippet证据直接迁入mono。

- Parser交接重验输入hash、解析版本、位置集合、canonical JSON实际hash/字节数；篡改解析内容但保留原始hash仍拒绝。PDF需要Parser给出的页码，web不允许制造PDF页码。此域校验不能替代ContentStore原文字节回读、候选授权或SSRF检查。
- Source使用版本化arXiv自然键，其他已下载原文暂用规范final URL + 原始hash；未确认的DOI和未版本化arXiv不伪造版本身份。标题/provider ID/URL fragment不改变身份；arXiv不标peer_reviewed。多入口provenance合并仍由正式事实merge处理。
- Evidence只能引用真实块索引与原文精确子串；table/formula必须连同caption/notes完整保留。ID来自Source、完整Location与规范quote；正文内容hash保持原文hash。模型不能自己提供位置或任意Source ID。
- Coverage基于关系、来源tier和显式条件重新计算，不相信模型的supported状态；支持与反驳并存为limited。没有抽出Claim的Spec仍产生Gap；已有supported Claim不掩盖同Spec下另一个缺证Claim。未知关系引用明确失败。

验证（backend）：

```bash
uv run pytest -q tests/unit tests/contract/test_fetch.py tests/contract/test_content.py
uv run pytest -q tests/contract/test_document_parser.py tests/contract/test_document_content.py tests/integration/test_mono_document_content.py tests/integration/test_mono_fetched_document.py
```

第一组 **370 passed in 16.79s**（含新增24项），第二组 **43 passed in 0.45s**。Ruff/format/diff检查通过。PDF块和页码使用明确标注的受控fixture，未声称真实PDF解析；MinIO集成沿用独立测试桶，仅操作测试资源。

**边界：这些函数尚未接入正式research worker或CLI research。** Run搜索/Fetch工具绑定、原文读权限、Claim条件ID/Observation抽取、受限追溯和逐查询提交尚待实现；T023/T026/T027/T043不勾选，不认定M3完成。上文DNS/TLS为历史环境故障记录，不能当作修复后当前环境的诊断。

## 正式Run Fetch绑定（T020/T026/T027部分）

`application/fetch_tools.py` 新增FetchBinding与Run-owned FetchTools，复用DocumentFetchPort和既有HTTPDocumentFetch，而不是再建下载器/对象库。PhaseTools与RunDriver接受显式绑定。

- 每个query callback拥有独立候选注册表；只能Fetch本次SearchBatch确实返回的候选，key为完整已校验候选的canonical hash。Worker不能改URL、metadata、类别、parser配置、预算或replay；新query没有Search授权前，即使原文已成功缓存也拒绝访问。
- 原文目标和类型构成Fetch语义输入，parser配置/适配版本进入identity；不把query、章节、搜索摘要或不断变化的Claim状态混入下载cache key。另一个query重新取得同一候选后，可以复用本Run已有不可变原文，不重复下载。不同Run不能复用他人的内容引用。
- 原文缺目标在预留前拒绝；实际Fetch经既有ToolCallService预留/结算fetch budget与attempt。工具缓存只保存FetchedDocument引用，正文保存在50MiB内容存储，避免复制大正文到10MiB工具cache。
- 返回或缓存重放均回读原文/解析对象，校验Run前缀、hash寻址key、冻结parser版本、结构与完整原文；成功缓存的原文缺失/损坏不会授权再次下载。回读受fetch timeout/Run剩余deadline约束，不因命中cache无限等待。

验证（backend）：

```bash
uv run pytest -q --tb=short tests/integration/test_mono_fetch_tools.py tests/integration/test_mono_search_tools.py tests/integration/test_mono_phase_tools.py tests/integration/test_mono_fetched_document.py
```

**24 passed in 10.06s**，其中新增Fetch集成2项。真实PG、真实MinIO、真实HTML Parser、真正httpcore/限制下载器通过受控socket响应获取原文字节；验证跨章节query仍只有一次下载/一次fetch attempt/一次预算消费，并回链Source与有行号Evidence；删除原文后明确失败，网络连接数不增加。

最终全量 **833 passed in 141.93s**；Ruff/format和 `git diff --check` 通过。测试仅创建并清理唯一隔离数据库/桶；无推送。

**不是公网原文/PDF或正式研究Agent验收。** 搜索provider和业务worker为受控fixture，Source/Evidence门在测试中验证，未由正式worker提交事实。正式research抽取、Claim/Observation、受限追溯、真正MinerU PDF及CLI仍缺；T020/T026/T027/T043继续不勾选。用户库/volumes、`.env`和 `docs/implementation/` 不变。

## 正式research worker与原文抽取（T023/T027部分）

实现 `application/phase_workers.py::research_worker` 和 `domain/research/agents/extraction.py`，不调用legacy Scout。Architect与研究抽取复用移出的 `agents/structured.py`；原来的重试/修复实现已从Architect删除，避免复制两套有界循环。

- 模型只提出当前章节Spec下的原文quote、SRO/conditions/关系和Observation，不接受模型给出的Source/Evidence/Claim ID、位置、status。代码生成稳定ID，Claim的条件变化产生新ID；Spec关联不是自然键，跨章同一Claim追加关联而不制造重复Claim，不能借此新增目标章之外的Spec或覆盖既有主张内容。
- Quote和完整表格/公式范围、块索引、Spec/关系引用、raw值/单位/标签、原始数值/uncertainty均校验。无关系先insufficient，coverage依据来源门、显式条件与支持/限制/反驳重算；共享Claim会考虑全部已关联Spec的要求。
- Observation不换算百分比、不计算差值，保留raw值和原始source_block/caption/notes。原文明确的十进制零不会因模型漏value变成null；缺失/歧义数字仍null。数值子串不能截取另一个原数字。这里的机械范围校验不证明模型语义/表格行列归属已正确，仍需Analyze/Critic和真实抽查。
- 查询单元Search→授权Fetch→原文抽取→Source/Evidence/Claim/Link/Observation/coverage作为完整PhaseResult交给既有单元提交；coverage单元不重新搜索。真正空结果保留未满足Spec的Gap；全部provider失败明确失败；存储/hash/控制错误不当来源降级。
- 每query最多检查4个去重原文目标；以查询/anchor相关词排序选择完整原文块，最多12块/32KiB JSON。不切块、不裁表；未检查的候选/块作为degradation记录，不宣称整篇已读。提取prompt上限96KiB，输出上限64K字符。未实现追溯/定向补查或“预算耗尽后最终收缩”，不能把候选/块上限当这些能力的替代品。

验证：

```bash
uv run pytest -q --tb=short tests/unit/test_scout_extraction.py tests/unit/test_phase_contracts.py tests/unit/test_scout_originals.py tests/integration/test_mono_research_worker.py tests/integration/test_mono_phase_tools.py
```

**81 passed in 4.98s**。新的Driver集成真正运行plan_worker/research_worker（模型和搜索结果受控），HTTP下载器/HTML Parser/PG/MinIO真实：五章10个research单元各有checkpoint，到analyze时seq=14；同一原文1次Fetch，最终1 Source/1 Evidence/1共享Claim（spec-1..5）/1 Observation，五章coverage回链。空搜索零Fetch/零原文/零Claim，但各Spec有Gap。Analyze未注册，Driver明确未配置失败，没有Report或假completed。

结构化反例包括裁切表格、越界原文、章节越权、未知引用、虚构值/误归一化、unit/header/uncertainty捏造、重复quote键、条件新ID、零值，以及一次quote修复后成功或明确失败。

**尚未提供CLI正式research或真实供应商/公网论文证据。** KB请求明确未配置，受限追溯/补查、相关派生数据返工失效、MinerU、CLI默认/HTTP组合仍未完成。T023/T027继续不勾选；用户数据/配置不变。

首次全量848通过/1失败：旧测试仍mock Architect已迁出的私有重试函数。迁到共享structured模块后定向19项通过，最终全量 **849 passed in 144.95s**；Ruff/format（10文件）及diff检查通过。无真实供应商付费调用、无用户数据改动或推送。

## 2026-10-07：真实本地 MinerU PDF 解析（T043 部分）

固定 MinerU 4.0.10 的可选 parser 依赖及 lock 已安装；安装后全量861项通过。显式准备脚本按两个公开仓库固定commit下载，生成文件size/hash清单，并使用MinerU自身的下载完成/ready检查，不手工伪造模型完成标记：

- opendatalab/MinerU-4_models_torch：`2b3afb86f4d23fa623f6b2f4b2279ed4ef93a89d`
- jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF：`9185688a0495e1577d521a757c7c0b62dd38ca48`
- 本轮模型目录：`/Users/hoyishui/.cache/dr4a/mineru-4.0.10-20261007`。本地Torch+llama.cpp；模型不提交Git。

本轮重新下载论文遇到TLS连接失败。未关闭证书/放宽SSRF；改用T028真实探针已保存的完整PDF，hash `bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697`，2,215,244 bytes。这只证明“真实已存原文→Parser”，不证明本轮重新下载或新的Source授权/研究Run。

```bash
uv run --no-sync python -m scripts.verify_pdf_parser \
  --content-key research-content/329afba9-7658-44fa-baa1-c19ea15a90bb/bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697 \
  --models /Users/hoyishui/.cache/dr4a/mineru-4.0.10-20261007 \
  --json --record NEW_FILE
```

真实执行修正两项适配错误：使用SDK实际的header/footer/aside_text类型名；忽略空白文本块，但整份空内容与缺正文的表格/公式仍失败。macOS子进程剥离调用者凭据/配置、本地模型source、禁用外部LLM辅助并使用sandbox-exec禁止network访问；父进程读MinIO，子进程仅读临时原文/本地模型，关闭或超时终止所属进程组。Linux网络隔离与部署尚未完成，未配置时fail-closed。

- [失败记录](t043-pdf-local-real.json)：空白text导致parser_output_incomplete，未伪造成功。
- [成功真实记录](t043-pdf-local-text-real.json)：15页、132个文本块、5个公式、4张表；原始hash不变，原始page_idx转1-based页码，表格caption/完整HTML及公式保留。
- 对原PDF第8页渲染图与Table 2逐行对照：BLEU的28.4/41.8等可对应，但最后两行合并FLOPs单元格被SDK拆列，`3.3 ·`与`10^18`分入相邻列。不能以成功ParsedDocument声称所有数值/列归属可信；保留原始输出，不人工改表伪造Parser精度。后续Observation完整cell/指数/缺失值约束与风险处理必须补齐。

受控归一化/真实PDFium前置/真正子进程错误回收定向测试覆盖；全量869项通过（158.45s），这是最后空白文本修正前的全量结果；修正后的归一化/前置定向18项通过。Ruff与diff检查通过。未运行外部LLM或发布Report。本轮仍不勾选T043/T028：Linux部署、完整取消/资源故障验收、真实PDF→Research Source/Evidence/Observation与表格质量处理尚缺。
