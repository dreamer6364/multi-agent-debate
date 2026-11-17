# 面试答辩卡（追问高频 5 条，可自证口径）

> 用途：面试前 5 分钟扫读。每条结构 = **一句话结论 → 为什么是自证取舍 → 自证锚点 → 追问链 → 边界（如实说）**。
> 来源：ISS-014 面试官评审 A-2 / A-3 / B-5 / C-2 / C-5；整理于 2026-11-17。
> 原则：**先把取舍讲清，再承认边界**。不要辩护式堆术语——每条给出"我为什么这么选、证据在哪、什么时候要换方案"。

---

## §1 A-2："多 Agent"凭什么叫多 Agent？和串行调 LLM 区别在哪？

**一句话结论**：编排层是**刻意设计的确定性状态机**——13 个赛制环节的顺序、计时、打断、判胜必须由代码保证，这是赛制模拟的正确性底线；Agent 性体现在**节点内的多轮工具循环 + 跨节点上下文记忆**，而不是"让模型自己决定下一步干嘛"。

**展开**：
- 图的骨架固定（13 环节 DAG，如开篇立论→质询→驳论→攻辩→自由辩→结辩→评审→宣判），因为辩论赛制本身是强规则的：环节不能跳、计时不能交给模型幻觉、胜负按评分表权重由规则裁决；
- 每个辩手节点内是**真 Agent 行为**：Function Calling 多轮工具循环——`search_evidence`（本地 BM25 动态取材）、`search_web`（联网，provider 按 env 探测）、`check_argument`（Elenchus 论证自检，inproc/MCP 双模式）；模型自己决定调哪个、调几轮，工具结果回注后再出稿；
- 跨节点记忆：digest 五栏压缩 + 最近 6 条原文 + 破题要点按可见性矩阵注入；评委另拿摘录池与证据来源白名单；
- 所以准确说法是"**确定性编排 + 节点内自主工具调用**"，不是粗糙的"串行 LLM 调 13 次"。

**自证锚点**：`backend/src/debate/engine/nodes/phases.py`（环节节点与 `_ask`）、`engine/tools/agent_tools.py`（工具清单与循环）、`engine/agents/context.py`（可见性矩阵）、`SPEC.md §4.1/§4.4`、`CONSTRAINTS.md B4`。命令：`.venv/Scripts/python -m debate run --topic topic-01 --format lite --fake` 看 13 环节推进。

**追问链**：
- "为什么不用 AutoGPT 那种自主规划？" → 赛制场景下可预测性 > 自主性；自主 Agent 会跳环节、编造计时与胜负，用户无法复现一场比赛；我们要的是"模拟辩论赛"，不是"让 AI 自由聊天"。
- "评委不是 Agent？" → 评委是**有独立人格、视角与评分卡的结构化 Agent**（13 个角色文件独立 prompt），三评委并发评分 + 纠错重试 + 终裁；但评分输出用 JSON schema 强约束，因为要落库与画雷达图。

**边界（如实）**：节点间无动态协商/自适应轮次（可选增强：评委自适应追问轮次未做）；这就是"确定性状态机"的代价，面试直接承认，不要说成"已实现自主 Agent"。

---

## §2 A-3："10KB 语料谈什么 RAG？BM25 怎么做？召回怎么评估？"

**一句话结论**：这个规模的语料 BM25（jieba 分词 + 域加权 + 低分阈值）是**合理选型**——零模型依赖、零向量库、中文开箱即用；我不假装做了 embedding，但能讲清**何时该换、怎么换、怎么评估**。

**展开**：
- 语料现状：`evidence-base.md` **20 张证据卡**（约 5.7KB）+ `evidence-relationships.md`（约 3.9KB），共约 9.6KB；按域标注（科技/商业/心理/…）；
- 检索实现：jieba 分词去停用词 → `BM25Okapi` → **域加权**（同域 ×1.6、跨域 ×0.25 且需过高分门槛）→ 低分阈值过滤；每张卡按域与辩题匹配加权，避免"AI 依赖"辩题被商业卡带偏；
- 动态性：不仅预检索注入，`search_evidence` 工具让辩手在节点内按"辩题 + 对方最近发言"**逐条重新检索**（不同发言吃不同卡），并有兜底链（联网失败静默回落本地卡）；
- 升级路径（规模上万条时）：切 **embedding + BM25 混合检索 → rerank（cross-encoder）→ 分块策略 → 召回评估集 recall@k**；语料 10KB 时上向量库只会增加运维面而不提升效果。

**自证锚点**：`backend/src/debate/engine/rag/retriever.py`（BM25 + 域加权 + 阈值）、`rag/corpus.py`（卡结构）、`knowledge/`、`engine/tools/agent_tools.py::search_evidence`、`SPEC.md §4.4/§4.5`。命令：`.venv/Scripts/python -X utf8 ../issues/evidence/_dump_cards.py g01 pro`（本地 dump 检索命中卡，注意该探针在 issues/evidence 下、需按路径调整）。

**追问链**：
- "召回率多少？" → 如实说：**目前没有 recall@k 评估集**，这是已知短板；A-1 的 golden set（10 条辩题 + 三维 judge）评的是生成质量，不是检索召回；下一步是补 20 条 query→期望证据的标注集。
- "为什么不用 embedding？" → 成本与规模不匹配：20 张卡 BM25 全量打分 < 1ms，向量化还得引模型与向量库；等语料上万条再切混合检索，这是**演进顺序**问题。

**边界（如实）**：无 embedding / rerank / 分块 / 召回指标；跨域加权是规则不是 learned；这些是规模决策的结果，不是"忘了做"。

---

## §3 B-5："两个用户同时创建，多进程下你用什么保证只成一场？"

**一句话结论**：**当前是单实例语义**——compose 的 uvicorn 未开 `--workers`（Dockerfile 默认单 worker），配额 `asyncio.Lock` / EventBus / Cache 都在进程内，单 worker 下并发正确性由事件循环 + 锁保证；**多 worker 会把锁与总线拆成 N 份，必须换 Redis 分布式方案**——这是明确的迁移点，不是隐藏 bug。

**展开**：
- 单实例内的并发正确性：创建辩题走 `asyncio.Lock` + 数据库层唯一约束双保险，ISS-001 修过 TOCTOU，有 13/1 并发回归测试锁定；SSE 走进程内 EventBus；
- 缓存层已真实接入 Redis（`REDIS_URL` 配则 `RedisCache`，失败降级直读 DB；未配回落 `MemoryCache`）——**注意这只是旁路缓存，不承载状态语义**，EventBus 与配额锁仍是进程内；
- **多 worker 迁移点**（面试要给路线）：EventBus → Redis pub/sub；配额锁 → Redis 分布式锁 / 数据库行锁（`SELECT … FOR UPDATE`）；缓存本来就是 Redis，无需改；届时可安全开 `--workers N`。

**自证锚点**：`docker-compose.yml`（backend 无 workers 声明）、`backend/Dockerfile`（CMD 为 `uvicorn … --factory` 单 worker 默认）、`engine/runtime.py`、`services/event_bus.py` 模块注、`services/cache.py` 模块注（"不承载状态语义"）、`api/debates.py` 配额锁、`tests/api/test_debates.py::test_concurrent_create_*`、`SPEC.md ARCH A1`。提交 `29a8c3d`（Redis 只读旁路缓存接入）。

**追问链**：
- "为什么不现在就上 Redis 锁？" → 单机演示级场景引入分布式锁会增加故障面与延迟；我在注释和约束卡里标了迁移点，属于**按部署规模分阶段演进**，不是不知道。
- "缓存脏读会怎样？" → TTL 60s 兜底 + 写路径主动失效；Redis 故障时按 miss 直读 DB；用户看到的内容与直读恒等（有 `test_topics_cache.py` 验证）。

**边界（如实）**：多 worker 下配额锁失效 → TOCTOU 可复活；EventBus 跨进程不互达；这些在 `--workers N` 时真实存在，正确回答是"需要迁移，我不会在单实例假设下假装它已分布式"。

---

## §4 C-2："JWT 怎么注销？退出登录后 token 还能用吧？"

**一句话结论**：当前是 **stateless JWT 的取舍**——登出只删浏览器 Cookie，token 本体在 TTL（默认 24h）内仍有效；这是演示级单用户场景的**有意识简化**，不是没意识到。生产正解是 jti 黑名单 + 短 TTL + refresh 轮换，且黑名单可以复用**已经接入的 Redis**。

**展开**：
- 现状：`auth.py::logout` 只 `delete_cookie`；JWT 无 jti、无服务端会话；`JWT_EXPIRE_MINUTES` 默认 1440（24h），由 `JWT_SECRET` 签名，httpOnly + SameSite=lax + 生产 Secure；
- 为什么这么选：单用户本地演示场景，"登出"语义主要是清浏览器会话；引入服务端状态就放弃了纯 stateless 的简洁性；
- 正解路径（面试直接给）：**jti 黑名单**（logout 时把 jti 写 Redis，TTL = token 剩余有效期；鉴权时查黑名单）+ **访问 token TTL 缩到 15~30min** + **refresh token 轮换**；由于 Redis 缓存层已接入，加黑名单不引入新基础设施（叙事与 B-5 缓存层一致）。

**自证锚点**：`backend/src/debate/api/auth.py::logout`、`core/security.py`（JWT 签发/校验）、`api/app.py:112`（TTL 默认 1440）、`api/deps.py::get_current_user`、`services/cache.py`（Redis 后端）、`SPEC.md §5.2`。

**追问链**：
- "被盗 token 怎么办？" → 当前无服务端撤销能力，只能等 TTL 过期——所以生产必须补 jti 黑名单；面试如实承认，再把上面的方案讲出来。
- "为什么不用 session？" → 本机演示、单实例、无水平扩展需求时 stateless 足够；一旦需要即时吊销/多设备会话管理，才值得引入服务端状态（Redis session 或黑名单）。

**边界（如实）**：无即时吊销、无 refresh 轮换、无 token 家族撤销；这是取舍，回答时不要说成"已经做了注销"。

---

## §5 C-5："做过并发/压测吗？并发正确性怎么证明的？"

**一句话结论**：**没有专业压测工具**（无 locust/k6），并发正确性结论来自**请求级竞态回归测试**（ISS-001：两人同时创建恰好一个 202），规模边界是**单机演示级**；面试如实声明，并给出"要压测我会怎么做"。

**展开**：
- 已有：`tests/api/test_debates.py` 的并发创建测试（13/1 断言）、xdist 分库隔离（`mad_test_gwN`）避免测试互踩；配额锁 + DB 唯一约束双保险；
- 没有：locust/k6 脚本、SSE 长连接数上限测试、内存曲线、QPS 数据；
- 如果补（面试给方案）：locust 50 并发创建 + 50 SSE 长连接，观察连接数、内存、P95 延迟；先在小规模摸清瓶颈再定 SLA；并发瓶颈预期在 LLM 限速（`MIN_CALL_INTERVAL`）而非 Web 层。

**自证锚点**：`backend/tests/api/test_debates.py`（`test_concurrent_create_*`）、`tests/api/conftest.py`（xdist 分库）、`api/debates.py` 配额锁、`config.yaml model.min_call_interval`、`issues/ISS-001-quota-toctou.md`。

**追问链**：
- "50 并发会挂吗？" → 没实测，不编数字；Web 层是 FastAPI + 异步 DB，进程内锁覆盖创建路径；真正的瓶颈在外部 LLM 限流与 SSE 连接管理，所以要测才能下结论。
- "为什么不做？" → 项目定位是功能演示 + 正确性验证，规模上限受限于免费档 LLM RPM；把时间投在竞态回归与确定性测试上优先级更高。

**边界（如实）**：单机演示级；无 QPS/延迟/连接数数据；不要说"做过压测"。

---

## §6 C-3：E2E 覆盖怎么讲？（措辞降级，别被要求当场打开）

**一句话结论**：E2E 是**单条主链路冒烟**（Playwright 1 个文件：`frontend/e2e/arena.spec.ts`，注册→建题→开赛→直播终态→归档→导出）+ Vitest 9 项组件测试；**不宣称"E2E 覆盖体系"**——面试被要求打开也不会尴尬。

**展开**：
- 有一条真实跑通的 Playwright 主链路（FakeLLM 后端，秒级完成，含 SSE 直播终态与导出下载断言）；
- 前端行为的主力回归是 Vitest（9 项：直播 Hook / 组件 / XSS 防护），不是 E2E；
- 后端回归 298 项（引擎/评分/计时/打断/SSE/API/评测）+ 覆盖率 85%——**测试厚度在后端与单测**，不在 E2E 数量。

**简历/口述措辞对照**：
- ❌ 不要写："Playwright E2E 全覆盖 / E2E 覆盖完整用户旅程"；
- ✅ 改成："Playwright 跑通 1 条注册到归档导出的主链路冒烟，组件行为由 9 项 Vitest 覆盖；核心测试厚度在后端 298 项单测/集成测试（覆盖率 85%）"。

**自证锚点**：`frontend/e2e/arena.spec.ts`（唯一 1 条 test）、`frontend` Vitest 9 项、`README.md §验收状态`、`issues/ISS-014-interview-review-gaps.md C-3`。

**追问链**：
- "就一条 E2E 够吗？" → 诚实说不够，这是单机演示级的功能冒烟；补法是登录→选题→开赛→归档主链路已覆盖，下一步补评委评分异常/断线重连/多用户并发场景（TEST_PLAN §8 已列 E2E-LITE/FULL 人工签收口径）。
- "为什么不写 E2E 覆盖？" → 简历上我把措辞收紧到"单条主链路冒烟"，因为经不起当场打开逐条看的宣称不如不说；测试重心在能确定性复现的单测/集成层。

---

## §7 A-5："辩手突然集体复读，你怎么排查？"（AI 岗加分）

**一句话结论**：一场一个 12 位 `trace_id` 从 SSE 事件贯穿到每次 LLM 调用的结构化日志；按 id 过滤即可看到这位辩手每次发言的 tokens / 延迟 / 失败重试，直接区分"模型复读""超时降级""内容过滤误拦"，不靠翻实录猜。

**展开**：
- 结构化日志：Gateway 每次请求（含每次重试 attempt）输出一行 JSON——`trace_id / tier / model / attempt / ok / latency_ms / prompt_tokens / completion_tokens / error`；
- **不落 prompt/response 全文**（隐私 + 容量），只落元数据；日志写入是旁路，失败静默不影响比赛；
- trace_id 在 Web 由 service 开赛前生成、CLI/eval 由 runner 兜底，注入 `DebateResult` / ctx / gateway，所有 SSE 事件 data 自动携带；
- 质量趋势配合 A-1 的 LLM-as-judge 报告（立场坚守/引用诚信/论证质量按次落盘）——**日志看"发生了什么"，评测看"好不好"**。

**自证锚点**：`engine/llm/observability.py`、`engine/llm/gateway.py`（埋点）、`engine/runner.py`（trace_id 注入）、`services/debate_service.py`（SSE 贯穿）、`SPEC.md §4.10/§6.2`、`tests/unit/test_observability.py`。

**追问链**：
- "为什么不全量落 prompt？" → 辩题与证据卡可能含用户信息，单次调用可达 10k+ tokens，全量落盘既是隐私风险也是存储成本；元数据足够定位，需要复现时可按 trace_id 用同一 seed 重放。
- "日志和评测报告怎么配合？" → 日志是 per-call 事实（延迟/失败/用量），评测是 per-debate 质量分；"复读"先用日志确认是模型输出异常还是降级占位，再用 judge 的立场坚守/论证质量分量化退化程度。

---

## §8 B-4："CI 绿不绿看运气？"（测试稳定性）

**一句话结论**：终态**推送早于持久化**——测试等 EventBus 的终态事件而非墙钟轮询，满载慢的是 DB 写库排队，不影响测试判定；CI 另有失败复跑兜底。

**展开**：
- 历史：全量 `-n 4` 偶发"等待状态超时"、失败集合随机、单跑全过——根因是 `wait_status` 固定 30s 墙钟轮询，而终态落库在 `graph.invoke → drain → DB commit` 之后；
- 根治：`wait_status` 订阅该场 EventBus，终态 `status` 事件（在 `bus.close()` 前发出，早于 DB commit）一到立即返回；订阅前即时回读覆盖"终态已落库"竞态；裸 client 回落轮询兼容全部调用点；
- 双保险：CI 对全量失败自动 `--lf` 复跑一次甄别真红。

**自证锚点**：`tests/api/conftest.py::wait_status`、`tests/api/test_wait_status.py`（3 项回归）、`.github/workflows/ci.yml`、ISS-014 B-4。

**追问链**："为什么不直接加超时？" → 加超时只是掩盖，满载无上限；等事件是语义正确的同步点，且事件先于持久化，天然不受写库排队影响。

---

## 速查表（面试官一句话 → 先答哪句）

| 追问 | 第一句结论 |
|---|---|
| "凭什么叫多 Agent？" | "确定性状态机编排 + 节点内多轮工具循环，赛制正确性不能用自主性换。" |
| "10KB 语料谈 RAG？" | "BM25 是这个规模的正确选型；升级路径是混合检索 + rerank + recall@k 评估集。" |
| "多进程并发怎么保证？" | "当前单实例语义；迁移点是 Redis pub/sub + 分布式锁，已标注。" |
| "JWT 能注销吗？" | "stateless 取舍，TTL 内有效；生产补 jti 黑名单（复用 Redis）+ 短 TTL + refresh。" |
| "做过压测吗？" | "没有专业压测，只有请求级竞态回归；规模边界单机演示级，方案我给得出。" |
| "E2E 覆盖怎么样？" | "1 条注册到归档导出的主链路冒烟 + 9 项 Vitest；不宣称覆盖体系，测试厚度在后端。" |
| "辩手复读怎么排查？" | "trace_id 贯通 SSE 与每次调用日志（tokens/延迟/失败），按 id 过滤即定位；评测报告看质量退化。" |
| "CI 绿不绿看运气？" | "终态事件驱动等待（早于 DB 落库）+ 失败复跑兜底；不是靠加超时掩盖。" |
