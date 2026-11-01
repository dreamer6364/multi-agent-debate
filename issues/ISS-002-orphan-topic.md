# ISS-002（P1）配额 429 时自建辩题已先落库成孤儿，用户重试即堆积重复辩题

**状态**：待修复
**位置**：`backend/src/debate/api/debates.py:85-95`（custom_topic 插入并 commit）、`:102`（其后才 `check_quota`）

## 结论

`create_debate` 处理 `custom_topic` 时，先插辩题行并 `commit`，**之后**才做配额检查。配额超限返回 429 时，辩题已经落库且无人回收——用户按前端提示重试一次就多一条一模一样的孤儿辩题。

## 复现

```bash
cd backend; .venv/Scripts/python.exe -X utf8 ..\issues\evidence\_diag_orphan_topic.py
```

1. 自建辩题创建第 1 场（202，正常）。
2. 再以另一条自建辩题创建第 2 场 → 429 `QUOTA_CONCURRENT`。
3. `GET /api/topics`：第 2 场的辩题已存在。
4. 连续重试 2 次 → 同名辩题共 3 条。

## 证据

```
first: 202
second: 429 {"code":"QUOTA_CONCURRENT","message":"已有进行中的比赛，请稍后再试"}
topics: [..., '测试辩题A / 辩题B', '测试辩题C / 辩题D']
ORPHAN_TOPIC_ON_429: True
topics after retries: 3
```

## 影响

- 用户可见的脏数据：辩题列表被同名条目刷屏，且无法批量清理（无去重、无回收）。
- 与 ISS-001 叠加：并发绕过配额时同样先落库，失败路径（引擎起不来等）也会留孤儿。
- 违反"失败不留半程状态"的工程约定（ARCH §13 收敛语义在写路径上没有贯彻到 API 层）。

## 修复建议

1. 顺序对调：先 `check_quota`，再插辩题、建场次（最小改动）。
2. 更彻底：辩题插入与场次插入放进同一事务，配额失败整体回滚（与 ISS-001 的方案 ② 天然合并）。
3. 可选加固：按 `owner_id + title` 对 custom_topic 去重复用，重试不再新增行。

## 验收标准

- `_diag_orphan_topic.py` 输出 `ORPHAN_TOPIC_ON_429: False` 且 `topics after retries: 0`。
- 回归用例：配额满时提交自建辩题 → 429 且 `GET /api/topics` 无新增行。
