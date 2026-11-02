# ISS-005（P2）API 测试共享全局 MySQL `mad_test` + 逐用例 TRUNCATE：无法并行、互踩即假红；数据库口令硬编码入库

**状态**：✅ 已修复（库按 xdist worker 隔离、口令外置、`pytest-xdist` 入 dev 依赖；验收：`pytest -n 4` 并行 3 次全绿——api 33 passed ×2 + 全量 225 passed ×1；`git grep` 全仓明文口令清零，含 `playwright.config.ts` 同类问题一并修复）
**位置**：`backend/tests/api/conftest.py:29`（`TEST_DB_URL`）、`:126-146`（`_mysql_clean_db` autouse TRUNCATE）

## 结论

1. 所有 API 集成测试共用一个**全局 MySQL 库** `mad_test`，每个用例前后 `TRUNCATE` 全表。任何并行执行（pytest-xdist、两个终端同时跑、CI 分片）都会互相清表，产生**与产品无关的假失败**。
2. 连接串把 MySQL 口令**硬编码并提交进仓库**（conftest.py:29，此处以 `root:****` 脱敏）——即使只是本地测试库口令，也违反"凭据不入库"的工程纪律（与 `.env` 出门禁的约定自相矛盾）。同类问题另见 `frontend/playwright.config.ts` 的 E2E 连接串（同一提交修复）。

## 复现

```powershell
# 终端 A
cd backend; .venv/Scripts/python.exe -m pytest tests/api/test_sse.py -q
# 终端 B（A 尚未跑完时）
cd backend; .venv/Scripts/python.exe -m pytest tests/api/test_sse.py::test_sse05_failed_debate_terminal -q
```

A 跑 sse05 的同时 B 触发 TRUNCATE，A 侧随即出现 `Duplicate entry 'alice' for key 'users'` 或 `wait_status ... 超时`（表被清空）。本次测试期间实际踩中：sse05 并行时表现为 `TimeoutError 等待 failed 超时`，串行单跑才暴露出真正的断言失败（ISS-003）。

## 证据

- `conftest.py:29`（修复前）：`TEST_DB_URL = "mysql+asyncmy://root:****@localhost:3306/mad_test?charset=utf8mb4"`（明文口令）
- `conftest.py:28` 注释自认："测试库全面切 MySQL（mad_test），用例间 TRUNCATE"
- 实测：并行双跑出现 `Duplicate entry 'alice'`；同一用例串行结果与并行结果不同（非确定性）。

## 影响

- 测试结果不可信：并行时的红灯无法区分"真回归"与"互踩"，浪费排查时间（本次已实际发生）。
- 硬编码口令入库：换机器/换人即失效，且若该口令在其他环境复用则是真实泄露面。
- 共享库还有残留风险：手动跑 dev 脚本误连 `mad_test` 会污染测试。

## 修复建议

1. 每个测试 worker 用独立库：`mad_test_w{worker_id}`（`PYTEST_XDIST_WORKER` 环境变量派生），或每用例独立 `CREATE DATABASE`。
2. TRUNCATE 前加 `IF EXISTS`，并改为 per-worker fixture 而非全局 autouse。
3. 口令移到环境变量 `TEST_MYSQL_URL`，conftest 缺省回落 SQLite（aiosqlite，文件已在 dev 依赖里）或直接 `pytest.skip`，仓库内只留 `.env.example` 键名。
4. 长期：MySQL 语义（唯一键、`asyncmy`）确有价值，保留 MySQL 但按 worker 隔离即可。

## 验收标准

- `pytest -n 4 tests/api` 连续 3 次全绿（并行稳定）。
- 仓库内不再出现明文 MySQL 口令；无 `TEST_MYSQL_URL` 时测试明确 skip 并提示配置方法。
