# ISS-014（评审）面试官视角：AI 应用岗短板与工程硬伤清单

**状态**：15 条全部处理（B-1/B-3/A-1/A-4/C-1/C-4/A-5/B-4 代码已修并全量验证；A-2/A-3/B-5/C-2/C-3/C-5 口径/措辞已整理；B-2 关键路径已实测通过、终态因 daemon 停止待补验）
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
- **事实**：~~`git grep eval/prompt_version/regression_set -- backend/src` = 0 命中~~ → **已建 `backend/eval/` 评测包**：golden set 10 条 + LLM-as-judge 三维评分脚本 + 报告落盘。提示词文件化（roles/rules/topics）保证**结构**可校验，评测体系保证**质量**可回归；FakeLLM 测试测流程编排，golden set 测输出质量。
- **面试官潜台词**："你改了一个词的 prompt，怎么证明立场稳定性没崩？"
- **修复建议**：建 10~30 条黄金辩题集（golden set）+ LLM-as-judge 三维评分脚本（立场一致 / 引用属实 / 逻辑谬误），prompt 改动前后跑分对比，分数落盘可查。
- **预估工时**：2~3h（10 条起步）
- **状态**：✅ 已修复（2026-11-17）
  - **资产**：`backend/eval/golden_set.jsonl` 10 条——3 条与内置 `topics/*.md` 逐字段对齐（单测锁定防漂移）+ 7 条新领域（教育/职场/医疗/环境/生活/文化等）防过拟合内置三题；
  - **judge**：`eval/judge.py` 三维评分（`stance_adherence` 立场坚守 / `citation_integrity` 引用诚信 / `argument_quality` 论证质量含谬误密度），双方各 0~10 + 理由；走 `tier="judge"` + `temperature=0.1`（低随机利于对比）；解析围栏剥离 → 首{末}截取 → 逐维严格校验 → 失败带纠错反馈重试 → 耗尽降级 `degraded=True`（单条失败不中断整轮，聚合剔除、全降级记 None 不拿 0 分冒充）；
  - **CLI**：`eval/run_eval.py`——`run_debate → judge → 报告落盘 eval/reports/<label>.json`（三维均分 + 实录 + token 用量）；`--label` 前后跑分对比、`--topics/--limit` 控成本、`--dry-run` 验资产（golden set + 配置 + 13 角色 Prompt，不出网）；
  - **门禁**：ruff 范围扩为 `src tests eval`（CI workflow + pyproject + issues/README）；
  - **测试**：`tests/unit/test_eval.py` 16 项（资产完整性/同步/坏行拒绝、judge 合法/围栏/越界/空理由、重试反馈与降级、聚合数学、CLI dry-run）；全量 pytest **274 passed** + ruff 0；
  - **冒烟证据**（2026-11-17，真实 GLM glm-4.5-flash）：`python -m eval.run_eval --topics g01 --format lite --label smoke-fixed` → **比赛 finished、23 条发言、judge 三维全出分**（立场坚守 正 7.0 / 反 8.0；引用诚信 8.0 / 9.0；论证质量 6.0 / 8.0；总均分 7.0 / 8.33），31 次调用、139,134 prompt tokens，报告 `eval/reports/smoke-fixed.json`（自含全场实录）。前两次冒烟因 ISS-015 内容过滤熔断（`smoke.json`/`smoke2.json`：比赛 failed 时 judge 对空实录如实给 0 分并写明理由、链路不中断——降级语义本身也得到验证）；资产修复后复跑即绿，**这正是一次"改动前后跑分对比"的实战演示**。

### A-2（P0）"多 Agent"实为固定工作流，易被质疑概念
- **事实**：LangGraph 图为预定义 13 环节 DAG，节点序固定，Agent 间无动态协商/规划/自主工具路由。
- **面试官潜台词**："这和串行调 LLM 的区别在哪？凭什么叫多 Agent？"
- **修复建议**（答辩口径，非代码）：主动拆解——编排层是**刻意的确定性状态机**（CONSTRAINTS B4：计时/打断/胜负判定必须确定性代码，赛制环节不可跳过），Agent 性体现在**节点内 Function Calling 多轮工具调用 + 跨节点上下文记忆**；把"为什么不用自主 Agent"讲成有依据的取舍（可预测性 > 自主性），而非知识盲区。可选代码增强：评委节点自适应追问轮次。
- **预估工时**：0（口径整理）；可选增强 2h
- **状态**：✅ 口径已整理（`issues/INTERVIEW-DEFENSE.md §1`：确定性状态机 + 节点内多轮工具循环的取舍；含"为什么不用自主 Agent""评委是不是 Agent"追问链；可选增强未做，面试如实声明）

### A-3（P1）RAG 撑不起追问：BM25 + 2 个文件
- **事实**：`backend/knowledge/` 仅 2 文件（evidence-base.md 6KB + evidence-relationships.md 4KB）；检索为 jieba + `BM25Okapi`，无 embedding/rerank/分块策略/召回率指标。
- **面试官潜台词**："语料 10KB 谈什么 RAG？混合检索怎么做的？召回怎么评估？"
- **修复建议**：保留 BM25（小语料 + 中文零依赖是合理选型），但必须能答出**升级路径**：语料上万条 → embedding 混合检索 + rerank + 召回率评估集（recall@k）。有余力补 20 条 query→期望证据 的小评估集。
- **预估工时**：0（口径）；评估集 1~2h
- **状态**：✅ 口径已整理（`issues/INTERVIEW-DEFENSE.md §2`：BM25 + 域加权选型依据、升级路径（混合检索 + rerank + recall@k）；如实承认无召回评估集；**证据卡实为 20 张约 9.6KB**，修正原"2 文件 10KB"口径）

### A-4（P1）无 token 成本护栏
- **事实**：`gateway.usage` 逐调用记账（calls/prompt_tokens/completion_tokens，ISS-012 BE-1 已做原子化），但全仓 `budget/cost_cap/max_cost` 仅命中质检**时间**预算，无 token/费用上限；日配额已删（`daily_limit: None`）。用户发起 full 赛制、或 prompt 死循环，token 消耗无上限拦截。
- **面试官潜台词**："生产上账单失控谁兜？"
- **修复建议**：单赛场次 token 硬上限（config 可配）→ 超限复用现有降级链优雅终止（记 `error` 终态，不静默截断）+ 回归测试。**与已有降级链叙事天然衔接，性价比极高**。
- **预估工时**：1~1.5h
- **状态**：✅ 已修复（2026-11-17）
  - **实现**：`ModelConfig.token_budget`（`config.yaml` 默认 200 万，0/None 不限）；Gateway 是每场新建的实例（`debate_service` / CLI / eval 均为单场工厂），在 `complete` / `stream` / `chat_with_tools` 调用前检查累计 usage（ISS-012 BE-1 原子记账）；超限抛 `TokenBudgetExceeded(DebateAborted)` → **runner 既有熔断分支直接复用**：`status=failed` + `error` 明细，不静默截断、不新增终态；四处工厂（API / CLI 真跑 / CLI fake / eval）全部注入；
  - **测试**：7 项（首枪通过第二枪前拦下不打后端/不退避、流式同、None/0 不限、终态复用、`_ask` 不吞成占位降级、图层端到端 failed+error、config.yaml 与默认值同步）；全量 pytest **292 passed**、ruff `src tests eval` 0；
  - **面试官口径**：按调用边界检查（单枪最多越界一个请求的量；工具循环按整轮入口检查，受 max_rounds 约束）；默认 200 万给足 full 赛制余量（lite 真模型冒烟实测 ~149k），收紧只需改 `config.yaml` 一处、零代码改动；FakeLLM 不产 token，既有测试天然不受影响。

### A-5（P2）LLM 调用零可观测性
- **事实**：全 `backend/src` 仅 3 处 `logging.getLogger`；无 prompt/response 落盘、无 trace_id 贯穿、无质量看板。
- **面试官潜台词**："辩手突然集体复读，你的排查路径？"
- **修复建议**：结构化日志（每次 LLM 调用记录 tier/model/tokens/latency + trace_id），trace_id 贯穿 SSE 事件便于回放定位。
- **预估工时**：2h
- **状态**：✅ 已修复（2026-11-17）
  - **trace_id**：12 位短 id，Web 由 `debate_service` 开赛前生成、CLI/eval 由 runner 兜底；经 `DebateResult.trace_id` → `DebateContext.trace_id` → `Gateway.trace_id` 贯通，**所有 SSE 事件 data**（含 speech/delta/status/引擎事件）与 LLM 调用日志同 id，一场一 id；
  - **结构化日志**：`engine/llm/observability.py` 的 `log_llm_call` 输出一行 JSON 到 `debate.llm` logger（event/trace_id/tier/model/attempt/ok/latency_ms/prompt_tokens/completion_tokens/error）——Gateway 的 complete/stream/chat_with_tools 每次请求（含每次重试）均埋点；**不记 prompt/response 全文**（隐私+容量），日志写入旁路失败静默；
  - **排查路径**（面试官追问直接答）：按 trace_id 过滤本场调用日志 → 看各辩手每次发言的 latency/tokens 与失败 attempt → token 异常低 / 大量 ContentFiltered / 长退避直接定位，不必翻 SSE 倒推；质量趋势配合 A-1 eval 报告；
  - **测试**：6 项（trace_id 唯一性、runner 注入网关与结果、ctx.emit 注入 SSE、`log_llm_call` JSON 字段与不含 payload、complete 成功/失败埋点、stream 埋点）+ SSE schema 两处更新为纳入 trace_id；SPEC §4.10 + §6.2 同步。

---

## 二、工程硬伤（会被现场验证）

### B-1（P0）自称"门禁"却无 CI
- **事实**：`.github/` 不存在；pytest/ruff/tsc/vite build 全靠本地手跑，push 无任何拦截。
- **面试官潜台词**："你的门禁在哪一步？我 clone 下来跑红算谁的？"
- **修复建议**：GitHub Actions 单 workflow 三 job（backend pytest+ruff / frontend tsc+vitest / vite build）。
- **预估工时**：0.5h
- **状态**：✅ 已修复（`.github/workflows/ci.yml`，backend job：MySQL 8 service + root 自动建 `mad_test_gwN` 隔离库 + `uv sync --frozen` + ruff + pytest -n 4（失败自动 `--lf` 复跑一次甄别 B-4 flake）；frontend job：`npm ci` + tsc + vitest + vite build；YAML 解析、`uv lock --check`、`uv run ruff`/`uv run pytest` 本地等价命令均验证通过。注：无 remote，Actions 实际首跑待推送到 GitHub 后确认）

### B-2（P0）Docker 构建从未实际验证
- **事实**：ISS-004 修复后 docker daemon 一直未启动，`docker build`/`compose up --build` 至今未跑通（`docker compose config --quiet` 仅静态校验通过）；"一键交付"是未兑现承诺。
- **面试官潜台词**：现场 demo 打脸风险最高的一条。
- **修复建议**：本机启动 Docker Desktop，实测 `docker compose up --build`，跑通健康检查 + 一场比赛。
- **预估工时**：0.5h（daemon 就绪后）
- **状态**：🟡 关键路径已实测通过，终态因环境停止未确认（2026-11-18）
  - **实测发现并修复的真实缺陷**：后端首次 `docker compose build` **失败**——容器内直连 pypi.org `ReadTimeout`，pip 误报 `No matching distribution found for fastapi`。修复 `backend/Dockerfile`：`pip install --index-url 清华镜像 --timeout 60 --retries 5`，并留 `--build-arg PIP_INDEX_URL` 供海外/企业源覆盖；修复后后端镜像 **BUILD=0**；
  - **已验证**：① 前端镜像 `tsc --noEmit && vite build` 容器内通过（1607 modules，4s）；② `docker compose up -d` 四容器（backend/frontend/mysql/redis）全 healthy；③ **B-3 迁移容器内实跑成功**（`alembic upgrade` 两条：baseline five tables → speeches add fallacies json）；④ 健康检查 `{"status":"ok","db":"ok"}`（经 nginx 反代）；⑤ 注册→登录→选题→开赛 API 全链路通过（debate id=1）；⑥ **容器内 GLM 真模型产出 51 条真实发言**（引擎正常推进，非 FakeLLM）；
  - **未完成**：比赛在 25 分钟观察窗口内**未达终态**——GLM 免费档限速（min_interval≈15s/调用）+ 输出冗长使 lite 赛发言数远超本地 FakeLLM 的 23 条，属真模型成本特性而非交付缺陷；随后 Docker daemon 因环境重启停止（npipe 断连），无法继续确认 finished；
  - **证据脚本**：`issues/evidence/_diag_b2_smoke.ps1`（纯 ASCII 输出，register→login→topics→debate→poll）。**结论**：一键交付链路（build/up/迁移/健康/开赛/真模型发言）已验证可用；"一场完整比赛 finished"待 daemon 持续运行时补验。

### B-3（P1）迁移没进部署路径——真实生产 bug
- **事实**：`init_db` 走 `SQLModel.metadata.create_all`（`db/session.py:25`），Dockerfile/compose 启动链**不跑 `alembic upgrade head`**；`alembic/versions/` 已有 2 个迁移。`create_all` 对已存在表**不加列**——上线后任何加字段的发布必然运行时报错。与 `CONSTRAINTS V4`（"要改表结构只走 Alembic"）口径矛盾：约束写了，部署没执行。
- **面试官潜台词**："数据库加一列，你怎么上线？" 答"create_all"直接暴露没做过真实迭代。
- **修复建议**：compose/backend 启动命令改 `alembic upgrade head && uvicorn ...`（或 entrypoint 脚本），Dockerfile 同步。
- **预估工时**：0.5h
- **状态**：✅ 已修复（`backend/Dockerfile`：COPY `alembic.ini`/`alembic/` 资产，CMD 改 `sh -c "alembic upgrade head && exec uvicorn ..."`；两个迁移幂等化——baseline 逐表 `has_table` 跳过、fallacies 列 `get_columns` 跳过，兼容历史 create_all 引导库不撞"表已存在"；新增 `tests/unit/test_migrations.py` 2 项回归：空库 upgrade 后 schema 与 `SQLModel.metadata.create_all` **逐列一致**（防"改模型忘写迁移"，实测 SCHEMA_IDENTICAL）+ 历史库幂等 upgrade 落 head、重复 upgrade no-op；README 生产交付段同步。注：镜像内实跑待 B-2 docker daemon 就绪）

### B-4（P1）测试套件不稳定（边界 flake 已知未根治）
- **事实**：2026-11-12 实测——全量 `-n 4` 连续 4 轮失败集合随机变化（2→4→2→3 个），全部为 `等待状态超时`，单跑全过；stash A/B 证明基线同样失败；靠 `wait_status` 15s→30s（ISS-005）压制；单轮全量 6~8 分钟。根因：测试含真实 wall-clock 等待（`conftest.py:82 time.sleep(0.05)`、引擎真实计时路径），满载即越线。
- **面试官潜台词**："CI 绿不绿看运气？"（与 B-1 叠加杀伤力翻倍）
- **修复建议**：短期在 CI 里标注已知 flake + 失败重跑一次；根治 = 引擎 sleep/时钟抽象注入（`now_fn` 已有先例，扩展到 sleep）。
- **预估工时**：口径 0.5h；根治 3h+
- **状态**：✅ 已修复（2026-11-17）
  - **根因**：`wait_status` 纯墙钟轮询（15s→30s）在满载下偶发越线；终态落库发生在 `graph.invoke → drain 排空 → _write_final DB commit` 之后，排队即超时；
  - **根治**：`wait_status` 改**事件驱动优先**——`new_client`/`new_live_client` 把 app 挂到 client 上，测试订阅该场 EventBus，终态 `status` 事件一到立即返回（事件在 `bus.close()` 前发出，**早于 DB commit**，不受写库排队影响）；**订阅前即时回读**覆盖"终态已落库"竞态；轮询降为裸 client / 事件错过的兜底（兼容所有 19 处调用点，零改动）；
  - **回归**：`tests/api/test_wait_status.py` 3 项（事件快路径在 GET 恒 running 下仍返回、终态已落库即时回读、裸 client 轮询兜底）+ API 全套 27 passed；CI 的 `--lf` 复跑保留作双保险；
  - **口径**：面试答"CI 靠不靠运气"——终态推送早于持久化，测试等事件不等墙钟；真实 LLM 看门狗（5400s）与测试 FakeLLM 时钟解耦；历史 flake 用事件总线根治，不是靠加超时掩盖。

### B-5（P1）单实例假设是多点地雷（已文档化，需主动声明）
- **事实**：EventBus 进程内、配额 `asyncio.Lock` 进程内、MemoryCache 每 worker 一份（REDIS_URL 未配置时多进程下**失效不互达 → 最长 60s 脏读；配额锁失效 → TOCTOU 复活**）。均已写进约束卡/代码注释，但 uvicorn 默认单 worker 掩盖了问题；一旦 `--workers N` 即踩中。
- **面试官潜台词**："两个用户同时创建，多进程下你用什么保证只成一场？"
- **修复建议**：答辩口径主动讲（ARCH A1 假设 + 与 EventBus 同期换 Redis 行锁的迁移点已在注释标注）；可选低成本加固：compose 显式声明单 worker（`--workers 1`）防误开。
- **预估工时**：0（口径）；worker 钉死 0.25h
- **状态**：✅ 口径已整理（`issues/INTERVIEW-DEFENSE.md §3`：单实例语义主动声明 + Redis pub/sub / 分布式锁迁移点；compose 未开 workers、Redis 仅旁路缓存不承载状态语义；含"为什么不现在上 Redis 锁""缓存脏读"追问链）

---

## 三、安全与产品层（次优先）

### C-1（P1）登录无暴力破解防护
- **事实**：`api/auth.py` 无失败计数/锁定/退避，仅靠 bcrypt rounds=12 拖慢单次；日配额删除后也无任何频率限制。
- **修复建议**：同用户名失败计数 + 指数退避（进程内即可，单实例假设一致），或接 IP 级限流中间件。
- **预估工时**：1h
- **状态**：✅ 已修复（2026-11-17）
  - **实现**：`api/login_guard.py` 的 `LoginGuard` 挂 `AppState.login_guard`（每应用一份：生产单实例语义，API 测试每应用新建 state 天然隔离）；规范化用户名（去空格/小写）连续失败 5 次锁定 30s、此后每多失败一次翻倍（上限 900s），成功登录清零；锁定中 429 `AUTH_RATE_LIMITED` 直接返回，不查库不跑 bcrypt；条目上限 4096 + 最久未活动淘汰防内存无界；
  - **测试**：单测 4（归一化/指数退避/封顶与淘汰/按用户隔离）+ API 集成 2（连败锁定→429→到期解锁、不株连他人与成功清零）；全量 pytest **298 passed**、ruff `src tests eval` 0；
  - **残余风险/面试口径**：进程内计数不跨 worker（多实例需 Redis 共享）；纯用户名锁定可被用于账户锁定 DoS（生产可补 IP 维度/验证码）；bcrypt 时序差导致的账号存在性枚举属既有问题、未纳入本次范围。

### C-2（P2）JWT 无法注销
- **事实**：logout 仅 `response.delete_cookie`（`auth.py:65-67`），token 本体 24h 内仍有效，无 jti 黑名单/refresh 轮换。
- **修复建议**：答辩口径讲清 stateless 取舍；正解是 jti 黑名单（可复用 Redis——与 ISS 缓存接入形成同一叙事）+ 短 TTL + refresh。
- **预估工时**：口径 0.5h；实现 2h
- **状态**：✅ 口径已整理（`issues/INTERVIEW-DEFENSE.md §4`：stateless 取舍 + jti 黑名单（复用已接入 Redis）+ 短 TTL + refresh 轮换正解；含"被盗 token 怎么办""为什么不用 session"追问链；实现 2h 未做，面试如实声明）

### C-3（P2）E2E 覆盖极薄
- **事实**：`frontend/e2e/` 仅 `arena.spec.ts` 1 个文件。
- **修复建议**：**简历措辞降级**（不要写"Playwright E2E 覆盖"，会被要求当场打开）；有余力补登录→选题→开赛→归档 1 条主链路。
- **预估工时**：措辞 0；补链路 2h
- **状态**：✅ 措辞已降级（0 代码；`issues/INTERVIEW-DEFENSE.md §6` + README 验收表同步）
  - **事实核准**：`frontend/e2e/arena.spec.ts` **1 个文件、1 条 test**（注册→建题→开赛→直播终态→归档→导出，FakeLLM 秒级）；前端行为回归主力是 Vitest 9 项，不是 E2E；
  - **降级口径**：简历/口述写"1 条注册到归档的主链路冒烟 + 9 项 Vitest；核心测试厚度在后端 298 项（覆盖率 85%）"，**不写"E2E 覆盖体系/全覆盖"**；README 验收表已明确"单条主链路冒烟"并附文件锚点；
  - **追问链**："就一条够吗"→ 如实不够，补法是 TEST_PLAN §8 已列的 E2E-LITE/FULL 人工签收 + 异常/断线场景。

### C-4（P2）覆盖率从未测量
- **事实**：dev 依赖无 pytest-cov；"251 个测试"不等于覆盖率。
- **修复建议**：`uv add --group dev pytest-cov`，CI 输出 coverage 报告，先看基线数字再定门槛（避免拍脑袋门槛卡死自己）。
- **预估工时**：0.5h
- **状态**：✅ 已修复（2026-11-17）
  - **依赖锁定**：`pytest-cov 7.1.0` + `coverage 7.17.3` 进 dev 组，`uv.lock` 同步（+142 行纯新增，无镜像 URL 污染）；
  - **CI 输出**：`Test with coverage` 步骤 `pytest -n 4 --cov=src/debate --cov-report=term-missing --cov-report=xml`，`coverage.xml` 经 `upload-artifact@v4` 上传（PR 可查）；**暂不设 `--cov-fail-under`**——先积累基线趋势再定门槛（避免拍脑袋卡死）；注意 `--cov=debate` 在此 editable 布局下抓不到数据，必须用 `--cov=src/debate`；
  - **基线数字（2026-11-17，真实全量）**：**TOTAL 85%（4474 语句 / 680 未覆盖）、298 passed / ruff 0**，报告 `backend/coverage.xml`（已入 .gitignore）；
  - **顺带修的 CI 隐患**：`uv sync --frozen` 会移除不在 lock 里的本地包 elenchus → CI 新增 `uv pip install ./third_party/elenchus` 步骤（实测 `uv run` 不二次移除）；这是 B-1 CI 从未真跑过的潜在缺陷，借 C-4 一并补上。

### C-5（P2）无并发/压测
- **事实**：无 locust/k6；所有"并发正确性"结论来自 2 请求级竞态测试（ISS-001）。
- **修复建议**：如实声明规模边界（单机演示级）；有余力用 locust 打 50 并发看 SSE 连接数与内存。
- **预估工时**：口径 0；压测 2h
- **状态**：✅ 口径已整理（`issues/INTERVIEW-DEFENSE.md §5`：如实声明单机演示级、无 locust/k6、结论来自请求级竞态回归；含"50 并发会挂吗""为什么不做"追问链与压测方案）

---

## 四、修复路线图（按面试 ROI 排序）

| 优先级 | 编号 | 项目 | 工时 | 消除的追问风险 |
|--------|------|------|------|----------------|
| 🔴 P0 | B-1 | GitHub Actions 三 job | 0.5h | ✅ 已修——"门禁在哪一步" |
| 🔴 P0 | B-2 | `docker compose up --build` 实测 | 0.5h | 🟡 关键路径已验（构建/迁移/健康/开赛/真模型发言），终态待 daemon 持续运行时补验 |
| 🔴 P0 | A-1 | golden set + LLM-as-judge 评分脚本 | 2~3h | ✅ 已修——**"prompt 改了怎么证明没变差"（AI 岗核心）** |
| 🟡 P1 | B-3 | alembic 进启动链路 | 0.5h | ✅ 已修——"加一列怎么上线"（真 bug） |
| 🟡 P1 | A-4 | 单赛场次 token 上限 + 超限终止 | 1~1.5h | ✅ 已修——**"账单失控谁兜"** |
| 🟡 P1 | C-1 | 登录失败限流 | 1h | ✅ 已修——**暴力破解追问** |
| 🟡 P1 | A-2/A-3/B-5/C-2/C-5 | 答辩口径整理（0 代码） | 0.5h | ✅ 已整理——`issues/INTERVIEW-DEFENSE.md`（5 条追问链 + 自证锚点 + 边界） |
| 🟢 P2 | C-4 | pytest-cov 进 CI | 0.5h | ✅ 已修——基线 **85% / 4474 语句**，XML 上传 PR |
| 🟢 P2 | A-5 | 结构化日志 + trace_id | 2h | ✅ 已修——**"复读怎么排查"（AI 岗加分）** |
| 🟢 P2 | C-3 | E2E 主链路或措辞降级 | 0~2h | ✅ 措辞已降级——不写"E2E 覆盖体系" |
| 🟢 P2 | B-4 | 测试时钟注入根治 flake | 3h+ | ✅ 已修——终态事件驱动替代墙钟轮询 |

**总预估**：P0 全清 ≈ 4h；P0+P1 ≈ 8h；全清 ≈ 13h。

---

## 复现/核验命令

```powershell
# A-1 评测体系（已建 backend/eval/）
cd backend; .venv/Scripts/python -m eval.run_eval --dry-run   # 期望：golden set 10 条 + 13 角色 OK

# B-1 无 CI
Test-Path .github/workflows                                        # 期望 False

# B-3 迁移未进部署链
git grep -n "alembic" -- backend/Dockerfile docker-compose.yml     # 期望 0 命中

# B-2 构建实测（需 docker daemon）
docker compose up --build -d

# A-4 单场 token 预算超限（已加 config.model.token_budget 护栏）
cd backend; .venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_iss014_token_budget.py -q   # 期望 7 passed

# C-1 登录失败限流（已加 AppState.login_guard + AUTH_RATE_LIMITED）
cd backend; .venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_login_guard.py tests/api/test_auth.py -q   # 期望 11 passed

# C-4 覆盖率（已进 CI，基线 85%）
cd backend; .venv/Scripts/python.exe -X utf8 -m pytest -q -n 4 --cov=src/debate --cov-report=term-missing   # TOTAL ≈ 85%
```
