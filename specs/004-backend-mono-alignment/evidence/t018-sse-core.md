# T018：广播与持久投影 SSE

2026-10-06，HTTP 默认组合根已接入 mono SSE；旧CLI仍使用显式legacy事件，不将旧队列称新契约。

严格五类事件 phase/progress/rework/error/done；每帧有 UUID id、命名 event 与完整JSON；共同字段 session/run/time/seq。progress摘要上限8KiB，同seq不同UUID不能合并。每订阅独立256条队列，慢消费者只断自己的流，不影响研究或其它订阅。

订阅先注册后读owner-scoped PG视图；冻结前409、他人资源404在HTTP headers前返回；bootstrap为当前phase或已提交done。默认5s轮询Run投影，15s注释心跳，Last-Event-ID不播放历史。跨进程进度只能补当前持久phase/done，不保证中间事件全量。对排在bootstrap之前的同seq旧状态重新核对PG，避免到达顺序使状态倒退。

done只能来自已提交终态；completed才提供报告URL。PG读取不可用只发送fatal diagnostic error并断开，不写失败、不开研究、不发假done。客户端断线在ASGI发送中和读取中都清理订阅，不等于cancel。open支持expires_at截止，但实际JWT过期时间的传递须在T053正式鉴权后接入，当前开发匿名无JWT。

测试：11项bus/真实PG订阅反例；独立广播与慢消费者、进度大小/同seq、先注册后bootstrap、迟到completed、owner/冻结前拒绝、跨进程failed投影、PG故障无假done、ASGI发送断线、心跳及未开始迭代的清理、同seq旧状态拦截。另有真实ASGI HTTP终态SSE检查命名done、HTTP headers、Last-Event-ID忽略与清理。全量361 passed，59.02s；Ruff/diff检查通过。

未完成：正式Orchestrator实时细粒度事件发布、JWT认证截止传递、独立TCP+SIGKILL活HTTP验收。T018及M2不勾选；本批受控报告不是cases质量验收。
