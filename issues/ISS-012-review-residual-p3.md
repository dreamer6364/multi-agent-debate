# ISS-012（P3）v2.9.4 全项目 bug 体检遗留低危项清单（代码审查发现，未修）

**状态**：登记待议（均为 P3 语义/边界瑕疵，无功能错误）
**来源**：2026-11-05 全项目体检（双 Explore 代码审查 + 全量测试）

## 后端

### BE-1 Gateway.usage 多线程 `+=` 丢更新
`engine/llm/gateway.py:207-209、267-269、391-393`——评委三线程并发 `complete()` 时
usage 字典的 `+=` 非原子，可能丢计数。影响 usage_json 统计精度。修法：usage 累加加锁
或改 `collections.Counter` + 单点汇总。

### BE-2 EventBus / _quota_locks 终态后永不回收
`services/debate_service.py:101、109、61-64`——每场的 EventBus（含 golden_quotes 列表）
赛后仍留在 `_buses`；每用户 quota lock 永不 pop。长期运行慢泄漏。修法：done_callback
里一并 pop `_buses`（保留需补发的窗口期可延迟到 close 后清理）；locks 用弱引用或上限。

### BE-3 SSE 事件 id 双空间混用
`services/event_bus.py:21-28` + `debate_service.py:146`——speech 用 seq、其余事件用
总线自增，两者数值重叠。当前重连用 after_seq 不消费 Last-Event-ID，无实害；未来若按
lastEventId 恢复会把两类序号混为一谈。修法：非 speech 事件 id 加偏移（如 10^7 起）。

### BE-4 流式 JSON 重试不发 attempt 重置信号
`engine/nodes/phases.py:280-301、317-345`——流式分支已把原始 JSON 逐 chunk 推给前端，
重试后的终稿与已推增量不一致且无 `on_delta(seq, "", attempt, ...)` 清空重画信号。前端以
speech 事件整体替换故无感，但 delta 累积路径存在脏文本残留风险。

### BE-5 runner 未预期异常路径不收割金句
`engine/runner.py:126-148`——drain_golden 只在 finished/cancelled/aborted 三条 return
路径调用；`graph.invoke` 抛其他异常时未收割结果随 close 遗弃（该场已判 failed，损失有限）。

### BE-6 环节时长配 0 会熔断整场
`engine/nodes/phases.py:245-247` + `sim/timer.py:16-18`——任一环节配 0 分钟 → 预算 0 →
`_ask` 防御分支 DebateAborted 判整场 failed。修法：0 时长环节跳过而非熔断。

### BE-7 stream_debate 窄窗口陈旧 running 收尾
`api/debates.py:343-349、369-374`——读取 terminal/bus.closed 与订阅之间引擎恰好终态时，
客户端收到陈旧 running 后流结束（重连自愈）。修法：收尾分支前重读一次 debate.status。

## 前端

### FE-1 applyDelta 无 seq 单调守卫
`stores/debateStore.ts:128-148`——服务端放弃某条发言且流已推进时，迟到旧 seq delta 会
重置 streaming 卡片顶掉当前内容（SSE 单连接内罕见，重放/代理场景可能）。

### FE-2 getSpeeches 的 has_more 被忽略
`api/client.ts:87-91`——初始补齐固定 limit 500，单场发言 >500 时归档回看静默截断。
修法：按 has_more 循环拉全。

### FE-3 store 的 goldenQuotes/swimlane 无上限（当前受后端封顶约束，无实害）
golden 受 max_total=16 封顶；swimlane 整份替换。仅在未来放宽后端上限时需重估。