# ISS-014（评审）面试官视角：AI 应用岗短板与工程硬伤清单

**状态**：待修复（本文档为"面试官视角评审"产出，非测试复现缺陷；逐条整改后更新状态）
**评审日期**：2026-11-17
**评审基线**：main @ `29a8c3d`（Redis 缓存接入后）
**性质**：与 ISS-001~013 的"测试复现缺陷"不同——本清单是**招聘方视角**的审查结论，按"面试被追问的杀伤力"排序。每条含：事实证据、面试官潜台词、修复建议、预估工时。

---

## 优先级定义（本清单）

| 级别 | 含义 |
|------|------|
| **P0** | 面试必被追问且答不上即扣分/一票否决；或真实生产 bug |
| **P1** | 大概率被追问；有明确低成本修复路径 |
| **P2** | 深挖才会暴露；或属于"如实声明边界即可"的取舍项 |

---

## 一、AI 应用岗专属短板（最危险）

### A-1（P0）无 Prompt/效果评测体系
- **事实**：`git grep eval/prompt_version/regression_set -- backend/src` = 0 命中。提示词文件化（roles/rules/topics）只保证**结构**可校验，不保证**质量**可回归；FakeLLM 测试测的是流程编排，不是输出质量。
- **面试官潜台词**："你改了一个词的 prompt，怎么证明立场稳定性没崩？"
- **修复建议**：建 10~30 条黄金辩题集（golden set）+ LLM-as-judge 三维评分脚本（立场一致 / 引用属实 / 逻辑谬误），prompt 改动前后跑分对比，分数落盘可查。
- **预估工时**：2~3h（10 条起步）
- **状态**：待修复

### A-2（P0）"多 Agent"实为固定工作流，易被质疑概念
- **事实**：LangGraph 图为预定义 13 环节 DAG，节点序固定，Agent 间无动态协商/规划/自主工具路由。
- **面试官潜台词**："这和串行调 LLM 的区别在哪？凭什么叫多 Agent？"
- **修复建议**（答辩口径，非代码）：主动拆解——编排层是**刻意的确定性状态机**（CONSTRAINTS B4：计时/打断/胜负判定必须确定性代码，赛制环节不可跳过），Agent 性体现在**节点内 Function Calling 多轮工具调用 + 跨节点上下文记忆**；把"为什么不用自主 Agent"讲成有依据的取舍（可预测性 > 自主性），而非知识盲区。可选代码增强：评委节点自适应追问轮次。
- **预估工时**：0（口径整理）；可选增强 2h
- **状态**：待修复（口径）

### A-3（P1）RAG 撑不起追问：BM25 + 2 个文件
- **事实**：`backend/knowledge/` 仅 2 文件（evidence-base.md 6KB + evidence-relationships.md 4KB）；检索为 jieba + `BM25Okapi`，无 embedding/rerank/分块策略/召回率指标。
- **面试官潜台词**："语料 10KB 谈什么 RAG？混合检索怎么做的？召回怎么评估？"
- **修复建议**：保留 BM25（小语料 + 中文零依赖是合理选型），但必须能答出**升级路径**：语料上万条 → embedding 混合检索 + rerank + 召回率评估集（recall@k）。有余力补 20 条 query→期望证据 的小评估集。
- **预估工时**：0（口径）；评估集 1~2h
- **状态**：待修复（口径）

### A-4（P1）无 token 成本护栏
- **事实**：`gateway.usage` 逐调用记账（calls/prompt_tokens/completion_tokens，ISS-012 BE-1 已做原子化），但全仓 `budget/cost_cap/max_cost` 仅命中质检**时间**预算，无 token/费用上限；日配额已删（`daily_limit: None`）。用户发起 full 赛制、或 prompt 死循环，token 消耗无上限拦截。
- **面试官潜台词**："生产上账单失控谁兜？"
- **修复建议**：单赛场次 token 硬上限（config 可配）→ 超限复用现有降级链优雅终止（记 `error` 终态，不静默截断）+ 回归测试。**与已有降级链叙事天然衔接，性价比极高**。
- **预估工时**：1~1.5h
- **状态**：待修复

### A-5（P2）LLM 调用零可观测性
- **事实**：全 `backend/src` 仅 3 处 `logging.getLogger`；无 prompt/response 落盘、无 trace_id 贯穿、无质量看板。
- **面试官潜台词**："辩手突然集体复读，你的排查路径？"
- **修复建议**：结构化日志（每次 LLM 调用记录 tier/model/tokens/latency + trace_id），trace_id 贯穿 SSE 事件便于回放定位。
- **预估工时**：2h
- **状态**：待修复

---

## 二、工程硬伤（会被现场验证）

### B-1（P0）自称"门禁"却无 CI
- **事实**：`.github/` 不存在；pytest/ruff/tsc/vite build 全靠本地手跑，push 无任何拦截。
- **面试官潜台词**："你的门禁在哪一步？我 clone 下来跑红算谁的？"
- **修复建议**：GitHub Actions 单 workflow 三 job（backend pytest+ruff / frontend tsc+vitest / vite build）。
- **预估工时**：0.5h
- **状态**：待修复

### B-2（P0）Docker 构建从未实际验证
- **事实**：ISS-004 修复后 docker daemon 一直未启动，`docker build`/`compose up --build` 至今未跑通（`docker compose config --quiet` 仅静态校验通过）；"一键交付"是未兑现承诺。
- **面试官潜台词**：现场 demo 打脸风险最高的一条。
- **修复建议**：本机启动 Docker Desktop，实测 `docker compose up --build`，跑通健康检查 + 一场比赛。
- **预估工时**：0.5h（daemon 就绪后）
- **状态**：待修复（阻塞：本机 daemon）

### B-3（P1）迁移没进部署路径——真实生产 bug
- **事实**：`init_db` 走 `SQLModel.metadata.create_all`（`db/session.py:25`），Dockerfile/compose 启动链**不跑 `alembic upgrade head`**；`alembic/versions/` 已有 2 个迁移。`create_all` 对已存在表**不加列**——上线后任何加字段的发布必然运行时报错。与 `CONSTRAINTS V4`（"要改表结构只走 Alembic"）口径矛盾：约束写了，部署没执行。
- **面试官潜台词**："数据库加一列，你怎么上线？" 答"create_all"直接暴露没做过真实迭代。
- **修复建议**：compose/backend 启动命令改 `alembic upgrade head && uvicorn ...`（或 entrypoint 脚本），Dockerfile 同步。
- **预估工时**：0.5h
- **状态**：待修复

### B-4（P1）测试套件不稳定（边界 flake 已知未根治）
- **事实**：2026-11-12 实测——全量 `-n 4` 连续 4 轮失败集合随机变化（2→4→2→3 个），全部为 `等待状态超时`，单跑全过；stash A/B 证明基线同样失败；靠 `wait_status` 15s→30s（ISS-005）压制；单轮全量 6~8 分钟。根因：测试含真实 wall-clock 等待（`conftest.py:82 time.sleep(0.05)`、引擎真实计时路径），满载即越线。
- **面试官潜台词**："CI 绿不绿看运气？"（与 B-1 叠加杀伤力翻倍）
- **修复建议**：短期在 CI 里标注已知 flake + 失败重跑一次；根治 = 引擎 sleep/时钟抽象注入（`now_fn` 已有先例，扩展到 sleep）。
- **预估工时**：口径 0.5h；根治 3h+
- **状态**：待修复

### B-5（P1）单实例假设是多点地雷（已文档化，需主动声明）
- **事实**：EventBus 进程内、配额 `asyncio.Lock` 进程内、MemoryCache 每 worker 一份（REDIS_URL 未配置时多进程下**失效不互达 → 最长 60s 脏读；配额锁失效 → TOCTOU 复活**）。均已写进约束卡/代码注释，但 uvicorn 默认单 worker 掩盖了问题；一旦 `--workers N` 即踩中。
- **面试官潜台词**："两个用户同时创建，多进程下你用什么保证只成一场？"
- **修复建议**：答辩口径主动讲（ARCH A1 假设 + 与 EventBus 同期换 Redis 行锁的迁移点已在注释标注）；可选低成本加固：compose 显式声明单 worker（`--workers 1`）防误开。
- **预估工时**：0（口径）；worker 钉死 0.25h
- **状态**：待修复（口径）

---

## 三、安全与产品层（次优先）

### C-1（P1）登录无暴力破解防护
- **事实**：`api/auth.py` 无失败计数/锁定/退避，仅靠 bcrypt rounds=12 拖慢单次；日配额删除后也无任何频率限制。
- **修复建议**：同用户名失败计数 + 指数退避（进程内即可，单实例假设一致），或接 IP 级限流中间件。
- **预估工时**：1h
- **状态**：待修复

### C-2（P2）JWT 无法注销
- **事实**：logout 仅 `response.delete_cookie`（`auth.py:65-67`），token 本体 24h 内仍有效，无 jti 黑名单/refresh 轮换。
- **修复建议**：答辩口径讲清 stateless 取舍；正解是 jti 黑名单（可复用 Redis——与 ISS 缓存接入形成同一叙事）+ 短 TTL + refresh。
- **预估工时**：口径 0.5h；实现 2h
- **状态**：待修复（口径）

### C-3（P2）E2E 覆盖极薄
- **事实**：`frontend/e2e/` 仅 `arena.spec.ts` 1 个文件。
- **修复建议**：**简历措辞降级**（不要写"Playwright E2E 覆盖"，会被要求当场打开）；有余力补登录→选题→开赛→归档 1 条主链路。
- **预估工时**：措辞 0；补链路 2h
- **状态**：待修复

### C-4（P2）覆盖率从未测量
- **事实**：dev 依赖无 pytest-cov；"251 个测试"不等于覆盖率。
- **修复建议**：`uv add --group dev pytest-cov`，CI 输出 coverage 报告，先看基线数字再定门槛（避免拍脑袋门槛卡死自己）。
- **预估工时**：0.5h
- **状态**：待修复

### C-5（P2）无并发/压测
- **事实**：无 locust/k6；所有"并发正确性"结论来自 2 请求级竞态测试（ISS-001）。
- **修复建议**：如实声明规模边界（单机演示级）；有余力用 locust 打 50 并发看 SSE 连接数与内存。
- **预估工时**：口径 0；压测 2h
- **状态**：待修复（口径）

---

## 四、修复路线图（按面试 ROI 排序）

| 优先级 | 编号 | 项目 | 工时 | 消除的追问风险 |
|--------|------|------|------|----------------|
| 🔴 P0 | B-1 | GitHub Actions 三 job | 0.5h | "门禁在哪一步" |
| 🔴 P0 | B-2 | `docker compose up --build` 实测 | 0.5h | 现场 demo 打脸 |
| 🔴 P0 | A-1 | golden set + LLM-as-judge 评分脚本 | 2~3h | **"prompt 改了怎么证明没变差"（AI 岗核心）** |
| 🟡 P1 | B-3 | alembic 进启动链路 | 0.5h | "加一列怎么上线"（真 bug） |
| 🟡 P1 | A-4 | 单赛场次 token 上限 + 超限终止 | 1~1.5h | "账单失控谁兜" |
| 🟡 P1 | C-1 | 登录失败限流 | 1h | 暴力破解追问 |
| 🟡 P1 | A-2/A-3/B-5/C-2/C-5 | 答辩口径整理（0 代码） | 0.5h | 概念/取舍类追问 |
| 🟢 P2 | C-4 | pytest-cov 进 CI | 0.5h | 覆盖率追问 |
| 🟢 P2 | A-5 | 结构化日志 + trace_id | 2h | "复读怎么排查"（AI 岗加分） |
| 🟢 P2 | C-3 | E2E 主链路或措辞降级 | 0~2h | E2E 追问 |
| 🟢 P2 | B-4 | 测试时钟注入根治 flake | 3h+ | "CI 靠运气" |

**总预估**：P0 全清 ≈ 4h；P0+P1 ≈ 8h；全清 ≈ 13h。

---

## 复现/核验命令

```powershell
# A-1 无评测体系
git grep -iE "eval|prompt_version|regression_set" -- backend/src   # 期望 0 命中

# B-1 无 CI
Test-Path .github/workflows                                        # 期望 False

# B-3 迁移未进部署链
git grep -n "alembic" -- backend/Dockerfile docker-compose.yml     # 期望 0 命中

# B-2 构建实测（需 docker daemon）
docker compose up --build -d

# C-1 无限流
git grep -nE "attempt|lockout|throttle" -- backend/src/debate/api/auth.py   # 期望 0 命中
```
