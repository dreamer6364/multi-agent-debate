# ISS-001（P0）配额"同时 1 场"被 TOCTOU 竞态绕过：并发创建两场都返回 202

**状态**：✅ 已修复（回归测试 `test_concurrent_create_race_only_one_accepted`）
**位置**：`backend/src/debate/services/debate_service.py:67-76`（`check_quota`）、`backend/src/debate/api/debates.py:102`（调用点）

## 结论

`check_quota` 用普通 SELECT 统计进行中场次，无任何锁；两个并发请求都能在对方 INSERT 之前通过检查，各自创建成功。SPEC/CONSTRAINTS A5 的"每用户同时 1 场"配额在并发下完全失效。

## 复现

```python
# 同一登录用户，同时发出两个创建请求
results = await asyncio.gather(
    c.post("/api/debates", json={"topic_id": 1, "format": "lite"}),
    c.post("/api/debates", json={"topic_id": 1, "format": "lite"}),
)
# 期望：一个 202、一个 429；实际：两个都 202
```

诊断脚本：`issues/evidence/_diag_race_auth.py`（串行复跑仍稳定失败）

## 证据

```
[FAIL] 并发创建仅 1 场成功 codes=[202, 202]
```

代码路径：`check_quota` 只读不锁（`debate_service.py:67`），`create_debate` 先 `check_quota` 后各自 INSERT，典型 check-then-act 竞态。

## 影响

- 配额契约失效：同用户可同时跑 N 场比赛，LLM 成本不受控（免费档下这是计费闸门）。
- 违反 SPEC §2.1 配额语义与 CONSTRAINTS A5；多场比赛并发写 EventBus/数据库，虽不崩但放大 ISS-011 类竞态窗口。

## 修复建议

任选其一（推荐 ①）：

1. **数据库锁**：`check_quota` 改在同一事务内 `SELECT ... FOR UPDATE` 锁该 `users` 行（或 MySQL `GET_LOCK(owner_id)` 命名锁），查完计数再插入，事务提交后释放。
2. **先插后验**：先 INSERT 场次，再在同一事务内 `COUNT(*)` 复查；超限则回滚本次 INSERT（自建辩题一并回滚，顺带消除 ISS-002）。
3. 唯一约束兜底：MySQL 无法直接建"部分唯一索引"，可用影子表/状态位辅助，复杂度最高，不推荐单独使用。

## 验收标准

- `_diag_race_auth.py` 中"并发创建仅 1 场成功"通过（恰好 1 个 202、其余 429）。
- 新增回归用例：N=5 并发创建，`202 计数 == 1`，且落库场次数 == 1。
