# 多 Agent 对抗辩论模拟平台 · 详细规格说明书（SPEC）

| 文档信息 | 内容 |
|---|---|
| 文档版本 | v2.0（对齐全栈架构 ARCHITECTURE.md v2.0） |
| 撰写日期 | 2026-10-24 |
| 关联文档 | [PRD.md](PRD.md)（需求）· [ARCHITECTURE.md](ARCHITECTURE.md)（架构） |
| 文档定位 | 细化到**可实现级别**：配置、数据库、API、SSE 协议、引擎行为、Prompt 资产、前端行为的完整规格。与架构文档冲突时，以本文为准 |
| 文档状态 | 待评审 |

---

## 1. 通用约定

| 约定 | 规格 |
|---|---|
| API Base | `/api`，JSON only（导出端点除外），UTF-8 |
| JSON 字段命名 | `snake_case`（与 DB、Pydantic 模型一致，零映射层） |
| 时间格式 | ISO 8601 带时区：`2026-10-24T09:30:00+08:00` |
| ID | 全部整型自增主键，JSON 中为 number |
| 鉴权 | httpOnly Cookie `access_token`（JWT，HS256），除 auth 与 health 外全部端点要求登录 |
| 错误响应 | 统一 `{"code": "<错误码>", "message": "<人读信息>"}`，见 §5.1 错误码表 |
| 分页 | `?page=1&page_size=20`（page_size ≤ 100），响应 `{items, total, page, page_size}` |
| 枚举值 | `camp`: `pro` / `con` / `neutral`；`format`: `full` / `lite`；`status`: `pending` / `running` / `finished` / `failed` / `stopped` |
| role_id 命名 | 辩手 `pro-1..4` / `con-1..4`；工作人员 `chair` / `timekeeper`；评委 `judge-logic` / `judge-content` / `judge-expression` |

---

## 2. 配置规格

### 2.1 `config.yaml`（赛制与业务参数，随仓库提交）

```yaml
procedure:
  speed_cpm: 220            # 模拟语速：字/分钟，时长↔字数换算基数
  phases:                   # 各环节时长（分钟），键为环节标识
    opening: 1.0            # 主席开场
    statement: 3.0          # 开篇立论（每人）
    cross_examination: 1.5  # 质询一辩（每组）
    rebuttal: 2.0           # 驳论（每人）
    attack: 2.0             # 攻辩盘问（每组）
    attack_summary: 1.5     # 攻辩小结（每人）
    free_debate: 4.0        # 自由辩论（每队）
    closing: 3.0            # 总结陈词（每人）
  free_debate_turn_cap: 0.5 # 自由辩单次发言上限（分钟，模拟短打）
model:
  temperature: { debater: 0.85, judge: 0.3, chair: 0.3, digest: 0.2 }
  max_retries: 3
  timeout_seconds: 60
context:
  recent_speeches: 6        # 注入最近原文条数 K
  max_input_tokens: 4000    # 单次调用输入预算，超限先砍 recent_speeches
  digest_max_chars: 1200
judges:
  count: 3                  # 1 | 3 | 5（5 时追加 judge-2/judge-4 通用评委）
quota:
  concurrent_debates_per_user: 1   # 每日场次不限（v1.3 修订）
conduct:
  abuse_keywords: ["滚蛋", "蠢", "废物", "无耻"]   # 人身攻击检测词表，可扩展；避免单字误报（如"回滚"）
  max_interrupts_per_answer: 2
export:
  output_dir: debate        # CLI 模式与导出缓存的相对目录
```

### 2.2 `.env`（密钥与环境，不入库不入 git）

| 变量 | 必填 | 示例 / 默认 |
|---|---|---|
| `OPENAI_API_KEY` | 是 | `sk-…` |
| `OPENAI_BASE_URL` | 是 | `https://open.bigmodel.cn/api/paas/v4` |
| `MODEL_DEBATER` | 是 | `glm-4.7`（辩手用主力模型） |
| `MODEL_OFFICIAL` | 是 | `glm-4.7-air`（评委/主席/摘要用廉价模型） |
| `MYSQL_URL` | 是 | `mysql+asyncmy://debate:pass@localhost:3306/mad?charset=utf8mb4` |
| `JWT_SECRET` | 是 | ≥ 32 字符随机串 |
| `JWT_EXPIRE_MINUTES` | 否 | `1440`（24 小时） |
| `CORS_ORIGINS` | 否 | `http://localhost:5173`（dev 前端地址） |

---

## 3. 数据库规格（MySQL 8，utf8mb4，Alembic 迁移）

### 3.1 `users`

| 字段 | 类型 | 约束 |
|---|---|---|
| id | BIGINT UNSIGNED | PK AUTO_INCREMENT |
| username | VARCHAR(32) | NOT NULL UNIQUE，正则 `^[a-zA-Z0-9_]{3,32}$` |
| password_hash | VARCHAR(72) | NOT NULL，bcrypt cost 12 |
| role | VARCHAR(16) | NOT NULL DEFAULT `'user'`（预留） |
| created_at | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP |

### 3.2 `topics`

| 字段 | 类型 | 约束 |
|---|---|---|
| id | BIGINT UNSIGNED | PK AUTO_INCREMENT |
| owner_id | BIGINT UNSIGNED | NULL FK→users.id ON DELETE CASCADE；NULL = 系统内置 |
| title | VARCHAR(200) | NOT NULL |
| pro_stance / con_stance | VARCHAR(500) | NOT NULL DEFAULT ''（v2.1 增补：引擎 Topic 需要双方立场） |
| pro_points | TEXT | NOT NULL，正方破题要点，换行分隔 |
| con_points | TEXT | NOT NULL，反方破题要点 |
| is_builtin | TINYINT(1) | NOT NULL DEFAULT 0 |
| created_at | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP |

索引：`idx_topics_owner(owner_id)`。内置辩题（含 PRD §7 三题）由迁移脚本种子写入，`owner_id=NULL, is_builtin=1`，全员只读。

### 3.3 `debates`

| 字段 | 类型 | 约束 |
|---|---|---|
| id | BIGINT UNSIGNED | PK AI |
| topic_id | BIGINT UNSIGNED | NOT NULL FK→topics.id（自定义辩题先落 topics 再引用） |
| owner_id | BIGINT UNSIGNED | NOT NULL FK→users.id ON DELETE CASCADE |
| format | VARCHAR(8) | NOT NULL，`full` / `lite` |
| status | VARCHAR(16) | NOT NULL DEFAULT `'pending'` |
| cfg_json | JSON | NOT NULL，快照：`{judges, speed_cpm, phase_durations}`（开赛时冻结） |
| winner | VARCHAR(8) | NULL，`pro` / `con` |
| best_speaker | VARCHAR(16) | NULL，如 `pro-3`（平票决胜与 MVP 展示用） |
| team_total_pro | INT | NULL，团体总分 |
| team_total_con | INT | NULL |
| votes_json | JSON | NULL，`{"pro": n, "con": n}`（v2.1 增补：导出评分表需要投票数） |
| usage_json | JSON | NULL，`{calls, prompt_tokens, completion_tokens, by_model:{}}` |
| error | TEXT | NULL，failed 时的人读原因 |
| started_at / finished_at | DATETIME | NULL |
| created_at | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP |

索引：`idx_debates_owner_status(owner_id, status)`、`idx_debates_topic(topic_id)`。

### 3.4 `speeches`

| 字段 | 类型 | 约束 |
|---|---|---|
| id | BIGINT UNSIGNED | PK AI |
| debate_id | BIGINT UNSIGNED | NOT NULL FK→debates.id ON DELETE CASCADE |
| seq | INT | NOT NULL，场内从 1 单调递增 |
| phase | INT | NOT NULL，1~10 |
| phase_name | VARCHAR(32) | NOT NULL，如 `质询一辩` |
| speaker_role | VARCHAR(16) | NOT NULL，§1 role_id |
| camp | VARCHAR(8) | NOT NULL，`pro` / `con` / `neutral` |
| text | MEDIUMTEXT | NOT NULL |
| side_notes | JSON | NULL，如 `["计时员：还剩 30 秒", "打断违规（超限）"]` |
| interrupted | TINYINT(1) | NOT NULL DEFAULT 0 |
| word_count | INT | NOT NULL |
| tokens | INT | NOT NULL DEFAULT 0 |
| created_at | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP |

约束：`UNIQUE uq_debate_seq(debate_id, seq)`；索引 `idx_speeches_debate(debate_id)`。

### 3.5 `scorecards`

| 字段 | 类型 | 约束 |
|---|---|---|
| id | BIGINT UNSIGNED | PK AI |
| debate_id | BIGINT UNSIGNED | NOT NULL FK→debates.id ON DELETE CASCADE |
| judge | VARCHAR(32) | NOT NULL，`judge-logic` 等 |
| focus | VARCHAR(16) | NOT NULL，`logic` / `content` / `expression` |
| scores_json | JSON | NOT NULL，§7.7 结构 |
| vote | VARCHAR(8) | NOT NULL，`pro` / `con` |
| best_speaker | VARCHAR(16) | NOT NULL |
| highlights_json / weaknesses_json | JSON | NOT NULL，双方各 2~3 条，须引用原话 |

约束：`UNIQUE(debate_id, judge)`。

---

## 4. 引擎行为规格

### 4.1 环节流程表（full 赛制，phase 1~10）

| phase | 名称 | 发言序列 | 单次预算 |
|---|---|---|---|
| 1 | 主席开场 | chair（介绍辩题/双方/评委/规则） | 220 字 |
| 2 | 开篇立论 | pro-1 → con-1 | 660 字 |
| 3 | 质询一辩 | (con-2 问 ↔ pro-1 答) → (pro-2 问 ↔ con-1 答) | 每组 330 字，问答共享 |
| 4 | 驳论 | con-2 → pro-2 | 440 字 |
| 5 | 攻辩盘问 | (pro-3 盘 ↔ 对方一/二/四辩轮流答) → (con-3 同理) | 每组 440 字 |
| 6 | 攻辩小结 | pro-3 → con-3 | 330 字 |
| 7 | 自由辩论 | pro 先，双方交替；单次 ≤110 字（`free_debate_turn_cap`） | 每队 880 字池 |
| 8 | 总结陈词 | con-4 → pro-4 | 660 字 |
| 9 | 评委点评 | 3 评委并行（各出 ScoreCard + 点评文本） | — |
| 10 | 宣判闭幕 | chair（投票、总分、胜方、致谢） | — |

lite 赛制仅执行 phase 2、4、7、8、9（发言序列不变）。

### 4.2 计时规格（确定性代码 `sim/timer.py`）

- **字数预算**：`budget = round(时长(分钟) × speed_cpm)`。
- **提醒点**：剩余预算 ≤ `round(speed_cpm × 0.5)`（默认 110 字 ≈ 剩 30 秒）时，插入旁注 `计时员：还剩 30 秒` + SSE `timer{warning}`，每环节至多一次。
- **叫停**：预算耗尽时，若该次调用尚未结束，**截断**文本至预算内最后一个完整句（。！？），追加旁注 `计时员：时间到` + SSE `timer{time_up}`；被截断的 `speech.interrupted=false`（打断仅指质询打断）。
- **自由辩论**：每队独立预算池（默认 880 字）；正方先手；一方池耗尽 → 系统旁注 `X 方时间用尽`，对方连讲至耗尽；双方耗尽环节结束。

### 4.3 打断规格（`sim/interrupt.py`）

- **模拟口径（v2.1 修订，LLM 调用离散化的落地解释）**：`[打断]` 标记只允许出现在**质询方（phase 3 提问方）的发言中**，作用于其上一条被质询回答——该回答被标记 `interrupted`、原文截断至约前一半的完整句；理由的句读归入理由文本。
- 触发语法：发言文本中出现 `[打断]<理由>`（全角【打断】等效），解析器提取并从正文剥离标记。
- 生效限制：仅 phase 3；同一被质询回答至多 `max_interrupts_per_answer`（默认 2）次；打断后由质询方继续追问。
- 违规处理：超限或未说明理由的标记不生效，追加旁注 `打断违规（超限或未说明理由）`，作为风度纪律项扣分依据写入 side_notes。

### 4.4 上下文组装规格（`agents/context.py`）

每次辩手调用注入五段（顺序固定）：

1. system = `rules/conduct.md`（六条守则全文）+ `roles/<role_id>.md`（角色全文）；
2. 辩题 + 本方立场 + 本方破题要点（来自 topics）；
3. 战局摘要 digest（≤ digest_max_chars）；
4. 最近 `recent_speeches`（默认 6）条原文，前缀 `[seq][camp-role]`；
5. 本环节任务指令：发言方、字数预算、环节句式要求（如驳论"对方说 X→但…"）、打断规则提示。

特例：phase 8 总结手与 phase 9 评委不注入"最近 6 条"，改注入**全场 digest 全量 + 各环节要点清单 + 原文摘录池**（digest 生成时随五栏抽取，每环节 ≤4 条原句，AGENT_CONSTRAINTS §6）。单次输入超 `max_input_tokens` 时按 **④最近原文 → ③digest → ②要点 → ⑤证据** 顺序裁剪（证据段最后裁）（v2.1 修订，原"4→2→3"为笔误；任务指令与 system 永不裁剪）。

### 4.5 digest 规格（`sim/digest.py`）

- 时机：每个 phase 结束后用 `MODEL_OFFICIAL`（温度 0.2）压缩本环节新增发言。
- 输出五栏结构（≤1200 字）：`定义与判准 / 论点与论据 / 交锋点 / 谬误指控 / 局势小结`。
- 合并策略：与上一版 digest 增量合并；合并后超长则先压缩旧栏再截断，**交锋点与谬误指控两栏永不丢弃**（保障四辩复盘）。

### 4.6 违规检测规格（代码侧，规则制）

| 违规 | 检测方式 | 处理 |
|---|---|---|
| 人身攻击 | `conduct.abuse_keywords` 词表命中 | 旁注 `违规：人身攻击`，风度纪律项建议扣 2 分 |
| 打断超限 | §4.3 | 旁注 + 扣分依据 |
| 数据造假 | 代码不判定；评委在 weaknesses 中引用具体发言判定 | 该论点作废 + 扣分（评委 rubric 注明） |
| 回应义务 | 同一问题第 3 次追问仍无对应回答（质询节点内计数） | 旁注 `回避追问`，评委 rubric 注明 |

### 4.7 评委输出 JSON Schema（`MODEL_OFFICIAL`，温度 0.3，失败重试 2 次）

```json
{
  "pro_scores":   { "statement": 0, "rebuttal_cross": 0, "attack": 0, "free": 0, "closing": 0, "conduct": 0 },
  "con_scores":   { "同左六维" },
  "vote": "pro | con",
  "best_speaker": "pro-3",
  "highlights":   { "pro": ["…（引用原话）"], "con": ["…"] },
  "weaknesses":   { "pro": ["…"], "con": ["…"] }
}
```

- 六维满分：statement 25 / rebuttal_cross 20 / attack 15 / free 20 / closing 15 / conduct 5（lite 赛制无 attack 维度，其 15 分按比例并入 rebuttal_cross）。
- highlights/weaknesses 每侧 2~3 条，评委回调系统校验条数，不满足则重试。
- 解析失败 2 次后：该评委记弃权卡（scores 全 0、vote 缺席、标注"输出异常弃权"）。

### 4.8 胜负判定规格（确定性代码）

1. 汇总 ScoreCard：团体总分 = 各评委六维之和的平均值（四舍五入）；
2. winner = vote 多数方；评委弃权不计入票数；
3. 平票（偶数评委或弃权导致）：比 team_totals，高者胜；
4. 再平：`best_speaker` 得票最多者所在队胜（best_speaker 取各评委提名众数）。

### 4.9 LLM 失败降级

重试 3 次耗尽 → 该次发言产出占位文本 `[系统] 该辩手超时未发言`，旁注 `LLM 降级`，流程继续；连续 3 次降级 → 引擎中止，debate.status=`failed`。

---

## 5. API 规格

### 5.1 错误码表

| code | HTTP | 场景 |
|---|---|---|
| `VALIDATION_ERROR` | 422 | 请求体校验失败（detail 附 pydantic 信息） |
| `AUTH_REQUIRED` | 401 | 未登录 / Cookie 过期 |
| `AUTH_INVALID` | 401 | 用户名或密码错误 |
| `USERNAME_TAKEN` | 409 | 注册重名 |
| `NOT_FOUND` | 404 | 资源不存在 |
| `FORBIDDEN` | 403 | 访问他人资源 / 删除内置辩题 |
| `QUOTA_CONCURRENT` | 429 | 已有进行中比赛 |
| `DEBATE_NOT_RUNNING` | 409 | stop 非运行中比赛 |
| `EXPORT_NOT_READY` | 409 | 比赛未 finished 即请求导出 |
| `INTERNAL_ERROR` | 500 | 兜底 |

### 5.2 端点规格

#### 认证

| 端点 | 请求 | 成功响应 | 错误 |
|---|---|---|---|
| `POST /api/auth/register` | `{username, password}`（密码 ≥ 8 位） | 201 `{id, username, created_at}` | USERNAME_TAKEN / VALIDATION_ERROR |
| `POST /api/auth/login` | `{username, password}` | 200 `{id, username}` + `Set-Cookie: access_token=…; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400`（生产加 `Secure`） | AUTH_INVALID |
| `POST /api/auth/logout` | — | 204（清 Cookie） | — |
| `GET /api/auth/me` | — | 200 `{id, username, role, quota: {daily_used, daily_limit}}` | AUTH_REQUIRED |

#### 辩题

| 端点 | 说明 |
|---|---|
| `GET /api/topics` | 200 `{items: [{id, title, pro_points, con_points, is_builtin, owner_id, created_at}]}`，内置 + 自己的 |
| `POST /api/topics` | 201；body `{title(≤200), pro_points, con_points}`；owner = 当前用户 |
| `DELETE /api/topics/{id}` | 204；仅属主；内置辩题返回 FORBIDDEN；被引用时 409 `TOPIC_IN_USE` |

#### 比赛

| 端点 | 说明 |
|---|---|
| `POST /api/debates` | body 二选一：`{topic_id, format, judges}` 或 `{custom_topic: {title, pro_points, con_points}, format, judges}`；judges ∈ 1/3/5；校验配额（先查 running 数，再查当日 count）→ 202 `{id, status: "pending"}`；后台启动引擎任务；错误：QUOTA_* / NOT_FOUND / VALIDATION_ERROR |
| `GET /api/debates?status=&page=` | 200 分页；仅返回 owner = 当前用户；item 含 topic 摘要与 winner |
| `GET /api/debates/{id}` | 200 完整详情：`{id, topic: {…}, format, status, cfg, winner, best_speaker, team_totals, usage, error, started_at, finished_at}` |
| `GET /api/debates/{id}/speeches?after_seq=0&limit=200` | 200 `{items: [speech…], has_more}`；seq 升序；after_seq 增量拉取 |
| `POST /api/debates/{id}/stop` | 仅 running → 协作式取消（引擎在节点边界检查取消标志）→ 200 `{status: "stopped"}`；否则 DEBATE_NOT_RUNNING |
| `GET /api/debates/{id}/export?format=md\|html` | 仅 finished；md → `text/markdown`，html → `text/html`；否则 EXPORT_NOT_READY |
| `GET /api/health` | 200 `{status:"ok", db:"ok"}`（免鉴权） |

所有比赛端点先做归属校验（owner_id ≠ current_user.id → 404，不泄露存在性）。

---

## 6. SSE 事件规格（`GET /api/debates/{id}/stream`）

### 6.1 连接协议

- 响应头：`Content-Type: text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`。
- **续传参数**：客户端以 `?after_seq=<最后收到的 speech.seq>` 连接（不用 header Last-Event-ID，便于自实现控制）。服务端行为：
  1. 重放 DB 中 `seq > after_seq` 的全部 speech（带原 side_notes）；
  2. 发送一条 `status` 快照 + 当前 `phase_start`（比赛进行中时）；
  3. 订阅实时流。
- **心跳**：每 15 秒发送注释行 `: ping`。
- **关闭条件**：收到终态（finished/failed/stopped）后服务端发送最终 `status` 事件并关闭。

### 6.2 事件类型与载荷（data 均为 JSON）

```
id: <speech 事件 = 该条 seq；其余事件 = 全局自增>
event: phase_start   data: {"phase": 3, "name": "质询一辩", "actors": ["con-2","pro-1"], "budget_words": 330}
event: speech_delta data: {"seq":12,"delta":"对方辩友","attempt":0}   # 逐字流式（v2.1）：仅直播期推送，不落库、断线重连不重放；attempt 变化表示流式重试，前端须重置该 seq 的累积文本
event: speech        data: {"seq":12,"speaker":"con-2","camp":"con","text":"…","interrupted":false,"side_notes":[],"word_count":86}
event: timer         data: {"type":"warning|time_up","speaker":"pro-1"}
event: interrupt     data: {"by":"con-2","reason":"请正面回答"}
event: phase_end     data: {"phase":3}
event: judging       data: {"judges":["judge-logic","judge-content","judge-expression"]}
event: verdict       data: {"votes":{"pro":2,"con":1},"team_totals":{"pro":82.3,"con":79.7},"winner":"pro","best_speaker":"pro-3","scorecards":[…3 张]}
event: status        data: {"status":"running|finished|failed|stopped","error":null}
event: error         data: {"message":"…"}
```

### 6.3 事件顺序保证

`phase_start → (speech | timer | interrupt)* → phase_end` 循环 → `judging → verdict → status(finished)`；speech.seq 严格单调递增且与 DB 一致。v1 **整条发言推送**，不做 token 级逐字流（v2 备选）。

---

## 7. 角色 Prompt 资产规格

### 7.1 文件清单（13 + 1）

`roles/`：`pro-1.md`~`pro-4.md`、`con-1.md`~`con-4.md`、`chair.md`、`timekeeper.md`、`judge-logic.md`、`judge-content.md`、`judge-expression.md`；`rules/conduct.md`（六条守则，PRD §6）。

### 7.2 角色文件统一模板（缺段视为加载错误）

```markdown
# <角色名：如 正方一辩 · 立论手>
## 身份      （一段：阵营、辩位、性格设定）
## 职责      （分条：核心职责、特殊权限/限制）
## 行为规则  （分条：含被质询/盘问时的应答纪律）
## 语言风格  （一段 + 3 个示例句式）
## 禁止事项  （分条，硬约束）
```

### 7.3 加载与拼接

`agents/loader.py` 校验模板段落齐全 → system prompt 拼接顺序：`conduct.md 全文` + `\n\n---\n\n` + `角色文件全文`。文件缺失或段缺失 → 启动即抛 `PromptAssetError`（fail-fast）。

---

## 8. 导出规格（`render/`，仅 finished 状态可导出）

| 产物 | 结构规格 |
|---|---|
| `transcript.md` | H1 `辩论实录：{title}` → 元信息块（赛制/日期/结果/用量）→ 每 phase 一节 `## 【环节 N｜名称】{参与者}，预算 {budget} 字` → 发言行 `**{camp}{role 中文名}**：{text}`，旁注为独立斜体行 `*（{side_note}）*`，被截断发言末尾加 `（被计时截断）` |
| `scorecard.md` | PRD §8.2 表格逐评委一行 → 胜负判定说明 → 最佳辩手 → LLM 用量小结表 |
| `report.html` | Jinja2 单文件内联 CSS；左侧环节导航锚点；角色配色 token：正方 `#2563eb` / 反方 `#dc2626` / 主席 `#6b7280` / 计时员 `#d97706` / 评委 `#a16207`；顶部胜方横幅；评分雷达图用内联 SVG（六维两组多边形）；零外部请求、零 JS |

---

## 9. 前端规格

### 9.1 路由表

| 路径 | 页面 | 鉴权 |
|---|---|---|
| `/login`、`/register` | 登录 / 注册 | 否（已登录重定向 `/topics`） |
| `/topics` | 辩题库 + 发起比赛弹窗 | 是 |
| `/debates` | 历史归档列表 | 是 |
| `/debates/:id` | 辩场直播 / 回看页 | 是（归属校验 404） |

### 9.2 `useDebateStream(debateId)` hook 行为

1. 先 `GET speeches?after_seq=0` 补历史 → 2. 打开 `EventSource(/api/debates/{id}/stream?after_seq={最后seq})` → 3. 按事件类型分发到 zustand store → 4. `onerror`：EventSource 原生重连（自带 Last-Event-ID 但被忽略）；连续失败 3 次降级为 **5 秒轮询** `speeches?after_seq` → 5. 收到终态 `status` 后 close。

### 9.3 zustand store（直播页）

```ts
{ status, phase, phaseName, speeches: Speech[], timerState: 'normal'|'warning'|'time_up',
  judging: boolean, verdict: Verdict | null, connected: boolean,
  appendSpeech, setPhase, setTimer, setVerdict, reset }
```

### 9.4 核心组件

| 组件 | 规格 |
|---|---|
| `PhaseTimeline` | 13 环节点（full）/ 5 点（lite），当前高亮、已完成打勾；点击已完成节点滚动定位 |
| `SpeechCard` | 左侧阵营色条 + 名牌（`正方·二辩`）；正文纯文本渲染（**禁止 `dangerouslySetInnerHTML`**，防 XSS）；旁注渲染为灰色徽标行；`interrupted` 显示 `已被打断` 徽标 |
| `TimerBar` | 由该环节累计 `word_count / budget` 计算进度（无逐字动画）；warning/time_up 时变色并复现旁注 |
| `ScoreBoard` | @ant-design/charts 雷达图（六维 × 双方）+ 团体总分柱状 + 各评委票型 |
| `VerdictBanner` | 胜方横幅 + 最佳辩手 + 平票决胜说明 |

---

## 10. 非功能规格

| 项 | 规格 |
|---|---|
| 直播延迟 | LLM 整条生成完即推送，端到端新增发言可见 < 2s（不含模型生成时间） |
| 单场成本上界 | full 赛制 60~80 次调用、≤ 300k tokens；lite 约 1/3；超预算风险由配额闸控制 |
| 并发 | 单实例 ≥ 10 个 SSE 订阅 + 1 场进行中比赛不劣化 |
| 安全 | bcrypt cost 12；JWT 24h；CORS 白名单；SQL 全走 ORM；前端纯文本渲染；Cookie `SameSite=Lax` |
| 浏览器兼容 | Chrome/Edge ≥ 120、Firefox ≥ 120、Safari ≥ 16.4（EventSource） |
| 日志 | 结构化 JSON 日志：`ts, level, debate_id, phase, event, tokens_ms`；`--verbose` 输出到 stdout |

---

## 11. 测试规格（与验收对照）

| 用例组 | 覆盖 | 对照 |
|---|---|---|
| `test_timer` | 预算换算、30 秒提醒唯一性、截断保留完整句、自由辩双池耗尽 | PRD AC-计时 |
| `test_interrupt` | 标记解析、2 次上限、违规旁注 | AC-打断 |
| `test_graph` | full/lite 节点裁剪、发言序列与 §4.1 一致 | AC-流程完整 |
| `test_scoring` | JSON 校验、弃权处理、平票→总分→best_speaker 三级决胜 | AC-胜负规则 |
| `test_context` | 五段注入顺序、超限裁剪顺序 ④→③→② | AC-无自说自话 |
| `test_api_auth` | 注册/登录/过期/归属隔离（他人资源 404） | 全栈新增 |
| `test_sse` | 事件顺序、after_seq 重放、终态关闭、心跳 | 全栈新增 |
| `test_quota` | 并发 1 场、日配额 429 | 全栈新增 |
| `test_export` | 三产物结构与 §8 一致（快照测试） | PRD §8 |
| E2E 冒烟 | lite + 廉价模型真跑一场，人工核对 PRD §10 | AC 全量 |

---

## 附录 A · PRD 全栈修订（随本 spec 生效，PRD 下次修订时合入）

1. §1.3 输出物改为：**平台直播（SSE）+ DB 归档 + 可选导出 md/html**；
2. §9 文件结构以 ARCHITECTURE.md §10（backend/frontend 分层）为准；
3. §10 验收标准追加三条：**SSE 断线重连不丢发言；未登录/非属主不可见他人比赛；配额拦截生效**；
4. §11 迭代规划 v2.0（自动化脚本）由本全栈平台取代，v2.1/v3.0 顺延。

## 附录 B · 开放问题（实现期决定，不阻塞开工）

1. 内置辩题初始数量（PRD 3 题起步，迁移种子可扩）；
2. 5 评委模式下通用评委的 focus 与 prompt 变体；
3. ~~发言 token 级流式~~ 已实现（v2.1）：`speech_delta` 事件 + 打字机渲染，见 §6.2；
4. `report.html` 雷达图内联 SVG 的具体绘制函数库选型（自绘 vs resvg）。
