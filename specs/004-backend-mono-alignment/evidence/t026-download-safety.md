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
