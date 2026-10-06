# T026部分进展：正文下载安全边界

日期：2026-10-06。**T026保持未完成**。此阶段不返回可用于引用的 Evidence 或完整 FetchedDocument；不会用搜索摘要、假正文、假页码填补Parser/存储能力。

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

- 正式 FetchPort.fetch(SourceCandidate) → FetchedDocument 及声明受控解析版本、内容引用、真实位置。
- 共享文档ContentStore的原始文件/解析块不可变存储、同键异hash拒绝、真实MinIO回读。
- 真实PDF经过本地Parser产生非空正文/表格/页码；可信摘录范围、hash和Source身份由T027连接验证。
- 正式PhaseTools/ToolCallService接入，不将此无Run的单次只读探针冒充预算/账本闭环。
- 在真实公网DNS环境再次下载论文并验证真实来源。当前未宣称论文取证通过。

未触碰已损坏的原PG数据或用户对象、未改backend/.env、未执行参考原型。
