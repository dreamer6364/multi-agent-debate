# ISS-003（P1）`test_sse.py` 4 个用例断言过期：行为演进（v2.2/v2.3）未同步测试，CI 基线失真

**状态**：✅ 已修复（`tests/api/test_sse.py` 7/7 通过；SPEC §6.2 事件示例同步 v2.3 字段与主席无 delta 说明）
**位置**：`backend/tests/api/test_sse.py:89-91 / :120 / :185 / :254、:260`

## 结论

全量 pytest 长期挂着 4 个失败。逐一取证后确认：**4 个全是测试断言过期，不是实现 bug**。后果是"红灯常态化"——真正的回归会淹没在既有失败里，测试丧失报警功能。

## 逐条定性

| 用例 | 过期断言 | 现实现（权威口径） |
|------|----------|--------------------|
| `test_sse01` L89-91 | `phase_starts == [2,4,7,8,9,10]` | SPEC §4.1 v2.2 起 lite 执行 `[1,2,4,7,8,9,10,11]`（仪式骨架 phase 1/11 必跑，SPEC.md:185-186） |
| `test_sse02` L120 | speech 字段集精确等于 7 个键 | v2.3 增补 `phase`/`phase_name`（`debate_service.py:135-137` 注释）；前端 `ArenaPage.tsx:197` 依赖 `s.phase === 1 \|\| s.phase_name === "主席开场"` 筛主席开场 |
| `test_sse05` L185 | 所有 speech 文本都是 `[系统] 该辩手超时未发言` | 主席发言（phase 1/11）走**确定性 fallback 模板**（`nodes/officials.py:85-105` `_keynote_fallback`/`_opening_fallback`），LLM 故障时仍是真实文本，不产占位 |
| `test_sse07` L260 | seq=1 定稿前必有 `speech_delta` | 同上：主席发言由代码模板直出、不经流式（`officials.py` 用 `gateway.complete` 而非 stream），seq 1/2 天然无 delta |

诊断脚本：`issues/evidence/_diag_sse.py`（实际 phase 序列、字段集、事件流取证）、`issues/evidence/_diag_sse05.py`（fault 场次 speech 文本取证）。

## 证据（实测）

```
phase_start: [1, 2, 4, 7, 8, 9, 10, 11]
speech data keys union: ['camp','interrupted','phase','phase_name','seq','side_notes','speaker','text','word_count']

# _diag_sse05.py（LLM 全故障）
speech count: 4
  seq=1 speaker=chair text='今晚的辩题是：人工智能时代…'      ← 确定性模板，非占位
  seq=2 speaker=chair text='欢迎来到本场辩论赛。…'           ← 同上
  seq=3 speaker=pro-1 text='[系统] 该辩手超时未发言'         ← 辩手才是占位
  seq=4 speaker=con-1 text='[系统] 该辩手超时未发言'
```

## 影响

- 全量 pytest 永远 4 红，掩盖真实回归（本次测试期间已实际干扰判断：并行跑时 sse05 呈现为超时，追查后才归因到 ISS-005 的互踩）。
- TEST_PLAN §SSE-02 "字段与 SPEC §6.2 完全一致（多余/缺失即 fail）"的意图仍然成立，但 SPEC §6.2 的示例字段表也未随 v2.3 增补更新——文档侧同步滞后。

## 修复建议

1. sse01：期望改为 `[1,2,4,7,8,9,10,11]`（phase_end 同步）。
2. sse02：期望字段集加 `phase`、`phase_name`；同步更新 SPEC §6.2 的 speech 事件字段示例。
3. sse05：改为"辩手（非 chair）的 speech 均为占位文本"，chair 的两条允许模板文本；尾部 `status{failed}`/熔断断言保持不变。
4. sse07：delta 断言排除 seq 1/2（chair），`checked >= 5` 调整为 `>= 3`（或改为"所有经 LLM 流式的 seq 都有 delta"）。

## 验收标准

- `cd backend; .venv/Scripts/python.exe -m pytest -q` → **0 failed**（当前 219 passed + 4 failed）。
- 断言仍守住原意图：SSE-02 字段精确匹配、SSE-05 终态收敛、SSE-07 delta 累积前缀语义。
