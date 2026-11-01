# 测试问题清单（Issues）

> 测试人角色产出：系统性测试（功能 / 并发 / 安全 / 部署 / 静态质量）发现的问题，按优先级排序。
> 测试日期：2026-11-01。基线：main @ `2310cbd`（后端 pytest 219 passed + 4 既有失败；前端 vitest 35 passed、tsc 0、vite build 0）。

## 优先级定义

| 级别 | 含义 |
|------|------|
| **P0** | 生产可被直接触发的数据/契约破坏，或安全边界被绕过 |
| **P1** | 主干功能在真实部署/日常 CI 中必然踩中的缺陷 |
| **P2** | 质量与可维护性缺陷：泄密风险、死配置、测试不可并行、静态检查债务 |
| **P3** | 体验/优化项，或仅静态审查怀疑、尚未稳定复现的问题 |

## 问题索引（按优先级排序）

| 编号 | 级别 | 标题 | 位置 |
|------|------|------|------|
| [ISS-001](ISS-001-quota-toctou.md) | P0 | 配额"同时 1 场"被 TOCTOU 竞态绕过：并发创建两场都返回 202 | `services/debate_service.py:67` |
| [ISS-002](ISS-002-orphan-topic.md) | P1 | 配额 429 时自建辩题已先落库成孤儿，用户重试即堆积重复辩题 | `api/debates.py:85-102` |
| [ISS-003](ISS-003-stale-sse-tests.md) | P1 | `test_sse.py` 4 个用例断言过期（v2.2/v2.3 行为演进未同步测试），CI 基线失真 | `tests/api/test_sse.py` |
| [ISS-004](ISS-004-dockerfile-broken.md) | P1 | Dockerfile 构建必失败（`COPY README.md*` 无匹配文件），且缺 `COPY knowledge`，容器内首场比赛必崩 `CorpusError` | `backend/Dockerfile` |
| [ISS-005](ISS-005-shared-test-db.md) | P2 | API 测试共享全局 MySQL `mad_test` + 逐用例 TRUNCATE：无法并行、互踩即假红；且口令硬编码入库 | `tests/api/conftest.py:29` |
| [ISS-006](ISS-006-tracked-debug-db.md) | P2 | 调试残留 `backend/._sse_dbg/test.db`（SQLite 61KB）被 git 追踪 | `backend/._sse_dbg/` |
| [ISS-007](ISS-007-ruff-debt.md) | P2 | ruff 共 176 处违规未清理，含 `runtime.py` 3 处 F821 `Any` 未导入（mypy 必挂）等真实缺陷信号 | `src/debate/engine/runtime.py:35` |
| [ISS-008](ISS-008-cors-dead-config.md) | P2 | `CORS_ORIGINS` 为死配置：`.env` 提供该变量但后端从未挂载 CORSMiddleware | `api/app.py` |
| [ISS-009](ISS-009-jwt-default-secret.md) | P2 | `JWT_SECRET` 缺省回落到内置 `dev-insecure-secret`：忘配环境变量即可伪造任意用户会话 | `api/app.py:87` |
| [ISS-010](ISS-010-bundle-size.md) | P3 | 前端单 chunk 1.17MB（gzip 374KB），超 Vite 500kB 告警线 | `frontend/dist` |
| [ISS-011](ISS-011-stop-finalize-race.md) | P3 | stop 与终态落库竞态：`_write_final` 无条件覆盖 status（静态审查发现，未稳定复现） | `services/debate_service.py:383` |

## 测试通过项（无问题记录，防止重复排查）

以下探测**全部通过**，不要当作待修问题：

- **越权（IDOR）13/13**：跨用户读/操作/导出/SSE 均 404，匿名均 401，bob 清空归档不波及 alice，内置辩题不可删（`evidence/_diag_security.py`）。
- **认证 6/6**：错 secret、过期、垃圾 token、alg=none 全部 401；合法 token 200（`evidence/_diag_race_auth.py`）。
- **行为边界**：非法 topic/format/judges、未结束导出、非法导出格式、`after_seq=-5`、`limit=0` 归一化、重复注册 409、弱密码/非法用户名 422（`evidence/_diag_behavior.py`，其中 2 个 FAIL 为断言预期写错，非产品问题）。
- **XSS 面**：导出 HTML 走 jinja `autoescape=True`（`render/html.py:77`）；前端全项目无 `dangerouslySetInnerHTML`/`innerHTML`。
- **密钥入库**：`backend/.env`（含真实 API Key）确认**未被 git 跟踪**（`.gitignore` 覆盖，`git cat-file -e HEAD:backend/.env` 不存在）。
- **SSE 断线重连**：先订阅再重放 DB、按 seq 去重；EventBus speech id = seq、非 speech id 单调自增（`services/event_bus.py`）。
- **前端**：vitest 35 passed、`tsc --noEmit` 0、`vite build` 0。

## 复现环境

- 后端：`cd backend; .venv/Scripts/python.exe -m pytest -q`（需本地 MySQL，库 `mad_test`，见 ISS-005）
- 诊断脚本：`cd backend; .venv/Scripts/python.exe -X utf8 ..\issues\evidence\_diag_xxx.py`（脚本内自建应用实例，不污染 dev 库）
- 前端：`cd frontend; npx vitest run; npx tsc --noEmit; npx vite build`
- 静态：`cd backend; .venv/Scripts/python.exe -m ruff check src tests`
- Docker：ISS-004 为静态取证（测试机 daemon 未启动，`docker build` 报 `failed to connect to the docker API at npipe`）
