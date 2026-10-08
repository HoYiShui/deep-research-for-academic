# T032：开发 Docker 沙箱执行内核

日期：2026-10-08；基线083c78c。设计来源：OPS §6开发隔离限制、MODEL §4附件与输出边界、API §6 CodeExecutionPort。用户授权恢复推进独立基础设施任务，不接管Agent策略。

## 范围与现状

旧DockerExecution未挂载script.py，non-root输出目录没有可写权限，超时只kill Docker CLI而不停止容器，失败返回空字典。旧contract测试仅mock进程并断言空输出，不能证明运行成功。

本批重建开发可信脚本内核，保留legacy execute(code,input,timeout) facade；execute_checked返回内核内部SandboxResult和已验证附件字节。这**不是**API中正式execute(AnalysisSpec,immutable inputs)接口，不授权模型产生任意代码。T031闭集模板编译／正式类型边界和业务worker尚未接入，T032暂不勾选。没有修改domain ports、Agent文件、共享State或报告结构。

## 执行与输出

- Python镜像按实得digest固定：`mirror.gcr.io/library/python@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f`；run使用pull=never，缺镜像不自动联网准备。镜像需要人工显式预取。Docker Hub auth token连续EOF后，显式拉取缓存镜像源成功，未改Docker全局配置或关闭TLS。
- 实际挂载script/input/supervisor为只读；network=none、只读root、UID/GID65534、CPU1、memory与memory-swap256MiB、pids64、cap-drop ALL、no-new-privileges；/tmp与/work/out分别为16MiB tmpfs，后者只对non-root用户可写。输出不挂宿主机可写目录，不以事后检查限制无界磁盘写入。
- Supervisor执行可信脚本，丢弃其stdout/stderr；检查result.json与显式files，只接受专用目录中的常规文件，O_NOFOLLOW/O_NONBLOCK拒绝symlink与特殊文件，拒绝额外未登记文件/目录、路径穿越、重复名字、空输出与非有限JSON。
- 输出及附件合计≤10MiB，传输envelope≤16MiB；host再次校验basename、JSON/CSV/PNG允许媒体、字节大小与内容，计算sha256，返回限定bytes。PNG检查格式签名，不声称完成复杂图像语义或渲染验收；SVG/HTML不接收。对象存储key由未来App层生成，不接受脚本提供的key。
- 容器CID由Docker写入未挂载的host临时目录；正常、输出拒绝、超时与协程取消均在收尾force-remove该精确CID，并kill/reap仍活Docker CLI。清理失败明确报错，不吞为成功。没有操作Compose/PostgreSQL容器或用户volume。

## 验证

`uv run --no-sync pytest -q tests/contract/test_execution.py`：**16 passed in 0.02s**。包括原无result的mock断言改为明确output_invalid、文件字节/hash/size/media、非法路径/SVG/PNG/UTF-8/nonfinite/结果结构、输入前置不调用Docker。

`uv run --no-sync pytest -q tests/integration/test_mono_sandbox.py --tb=short`：**9 passed in 6.16s**。没有mock Docker：实际输入求和/CSV输出及hash、UID65534、script/input/root不可写、外网连接失败；无输出/脚本抛异常/symlink/穿越/过大文件均非零失败，无私密异常正文；真实sleep容器超时和协程取消后，对照容器列表无新增遗留。执行后再次`docker ps --all --filter label=dr4a.sandbox=true`为空。

4个改动Python文件Ruff与format通过；初次Ruff对supervisor的泛化异常捕获报警，收窄为明确I/O/数据异常，没有忽略规则。本轮未调用LLM/Search，不迁移/清理用户数据库，不修改.env或docs/implementation。

最终全量回归：`uv run --no-sync pytest -q --tb=short`，**982 passed in 220.24s**；包含上述真实 Docker 测试。

## 未完成边界

T032正式闭集AnalysisSpec/ExecutionResult装配需T031协调；当前stdlib镜像不含绘图库，不声称plot模板或全部业务操作可用。附件仅为返回字节，尚未持久化/提供T037下载接口。生产无Docker socket与隔离Worker仍属T057。进程SIGKILL/daemon断连等不确定创建/清理窗口不能据本批普通取消测试宣称exactly-once或完整崩溃恢复；CID未写出前的启动窗口需后续进一步处理。
