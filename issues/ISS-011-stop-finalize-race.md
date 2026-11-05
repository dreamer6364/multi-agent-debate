# ISS-011（P3）stop 与终态落库竞态：`_write_final` 无条件覆盖 status（静态审查发现，未稳定复现）

**状态**：待复现/确认（静态审查结论，需决定是否为预期行为）
**位置**：`backend/src/debate/services/debate_service.py:383`（`_write_final` 直接 `debate.status = result.status`）

## 结论

`stop_debate` 把 `cancel_flag` 置位并把 status 写为 `stopped`；但引擎若已跑到终点（取消标志只在节点边界检查），`_job` 仍会走 `_finalize → _write_final`，**无条件**把 status 覆盖为 `result.status`（`finished`）。即：

- 用户点了"停止"，界面最终显示"已完成"——`stopped` 状态被吞。
- 反向窗口更小但存在：`_mark(stopped)` 若晚于 `_write_final`，则把 `finished` 改写成 `stopped`（代码注释 `debate_service.py:380` 已承认并发删除/写入的竞态面，`_finalize` 的去重兜底也源于同类历史问题，说明这条写路径确实有过并发事故）。

本次测试 stop pending/running 场次均正确得到 `stopped`（`evidence/_diag_behavior.py`），但"取消信号与引擎终点同窗口"的窄竞态未构造复现。

## 复现建议（供确认用）

```python
# 用 BlockingBackend 把引擎停在最后一个节点（closing_ceremony 之后、_finalize 之前）：
backend.release.set()          # 放行引擎
await client.post(f"/api/debates/{id}/stop")   # 同窗口按下停止
detail = await wait_status(client, id, "stopped")
# 观察：status 最终是 stopped 还是被 _write_final 覆盖成 finished
```

## 影响

- 语义层面：用户显式 stop 后看到 finished，审计/复盘时无法区分"自然结束"与"被叫停"。
- 数据层面：终稿落库本身无损（发言都在），主要是状态语义与体验问题，故定级 P3。

## 修复建议

1. `_write_final` 落库前重读 cancel_flag：`cancelled` 时 result.status 记为 `stopped`（或保留已写入的 `stopped`，仅覆盖内容字段）。
2. 用数据库条件更新兜底：`UPDATE debates SET status=:final WHERE id=:id AND status NOT IN ('stopped')`，让先到的终态胜出且不可被回退。
3. 先补一个确定性复现测试（上面脚本化），再决定实现口径——若团队认为"跑完就该算 finished"是预期，则把该语义写进 SPEC §5 并关闭本条。

## 验收标准

- 存在覆盖"stop 与终点同窗口"的回归测试；status 语义与 SPEC §5 一致（无论采纳哪种口径）。

## 已修复（2026-11-05，v2.9.4）

双向竞态均已修复：

1. **正向（stop 被 finished 覆盖）**：`_write_final` 落库前重读 `_cancel_flags`——
   `result.status == "finished"` 且旗标已置位时改写为 `stopped`（内容字段照常写全）。
2. **反向（迟到的 stopped 覆盖 finished）**：stop 端点的状态写入改为条件更新
   `UPDATE debates SET status='stopped' WHERE id=:id AND status IN ('pending','running')`，
   rowcount==0 说明终态先到，返回 409 不再覆盖。

回归：`tests/api/test_debates.py::test_stop_*` 全绿；`_write_final` 分支由全流程 stop 用例覆盖。
