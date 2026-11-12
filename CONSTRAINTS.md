# 约束卡（Constraints Card）

| 文档信息 | 内容 |
|---|---|
| 版本 | v1.0（2026-10-24） |
| 定位 | 实现期**硬边界速查表**：编码、评审、调试时先查本卡。是 PRD / ARCHITECTURE / SPEC 中约束的蒸馏，不引入新决策 |
| 冲突裁决 | 文档优先级：SPEC > ARCHITECTURE > PRD；本卡与三者有出入时**以 SPEC 为准**，并回改本卡 |
| 配套文档 | [PRD.md](PRD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [SPEC.md](SPEC.md) |

---

## 1. 技术栈锁定与端口

### 1.1 技术栈（锁死，换型 = 越界）

| 层 | 锁定选型 | 禁入替代品 |
|---|---|---|
| 引擎 | Python ≥ 3.11（uv 管依赖）、LangGraph、Pydantic v2、openai SDK | 其他 LLM SDK（zhipuai / anthropic SDK / langchain 全家桶） |
| 后端 | FastAPI、SQLModel + Alembic、MySQL 8（utf8mb4，asyncmy 驱动）、Jinja2 | Django/Flask、SQLAlchemy 裸用（SQLModel 底层除外）、MongoDB |
| 前端 | React 18 + Vite + TypeScript(strict)、Ant Design 5、TanStack Query、zustand、axios、@ant-design/charts | Next/SSR 框架、Vue、Tailwind/shadcn/MUI、Redux/MobX、原生 fetch 封装替代 axios |
| 实时 | SSE（EventSource + `?after_seq` 续传） | WebSocket、轮询（轮询仅作 §9.2 降级兜底） |
| 认证 | JWT + bcrypt + httpOnly Cookie | localStorage 存 token、Session 方案 |

**依赖纪律**：新增任何依赖前，先确认锁死清单无对应能力；确需引入 → 写明理由与被拒替代品 → 更新 ARCHITECTURE §1 后才可 `uv add` / `npm i`。

### 1.2 端口与地址（默认值锁死，冲突走环境覆盖）

| 服务 | 默认地址 | 说明 |
|---|---|---|
| 前端 dev（Vite） | `http://localhost:5173` | dev 代理 `/api` → 8000 |
| 后端 FastAPI | `http://localhost:8000` | `/api/docs` 自动文档 |
| MySQL | `localhost:3306` | 仅开发期向宿主机暴露 |
| 交付入口（nginx） | `http://localhost:80` | 托管前端构建产物 + 反代 `/api`，SSE 关闭缓冲 |
| 容器互联 | `backend:8000`、`mysql:3306`（compose 网络内） | 代码内禁止写 `localhost` 连 DB，一律读 `MYSQL_URL` |

端口冲突处理：改 `.env` / compose 映射覆盖并记录，**不改默认值本身**。

---

## 2. 服务边界（谁负责什么，越界即打回）

| # | 边界 | 允许 | 禁止 |
|---|---|---|---|
| B1 | 前端 ↔ 后端 | 前端只走 REST + SSE | 前端直连 LLM / 直读 DB；前端出现任何密钥 |
| B2 | 引擎 ↔ Web | `engine/` 只通过 `on_speech` / `on_event` 两钩子对外通信 | `engine/` import FastAPI、db/models、SQLModel（schema.py 自有数据结构除外） |
| B3 | API 层 | 只做鉴权、校验、配额、编排调用、序列化 | 在 `api/` 写辩论逻辑（计时、打断、评分规则归 `engine/sim`、`engine/nodes`） |
| B4 | 规则归属 | 计时换算、打断限制、胜负判定、违规检测 = **确定性代码** | 任何上述规则交给 LLM 自由判断 |
| B5 | 事实来源 | DB 是唯一事实来源；SSE 只是推送；断线/刷新一律从 REST 补齐 | SSE 承载状态语义、前端本地状态当真相 |
| B6 | Prompt 资产 | 角色/守则/辩题只存在于 `roles/ rules/ topics/` 文档 | system prompt 硬编码进 `.py` 字符串 |
| B7 | 写入权 | `debates / speeches / scorecards` 只能由 `debate_runner`（引擎回调）写入 | `api/` 或前端路径写这几张表（stop 只改 status） |
| B8 | 单实例假设 | EventBus 用进程内实现，预留替换接口；Redis 仅可作**只读旁路缓存**（`REDIS_URL` 可选配置，cache-aside，DB 仍是唯一事实来源，故障自动降级直读 DB） | 用 Redis 替换 EventBus / 引入消息队列 / Celery；让缓存承载状态语义（绕过 DB 读写） |
| B9 | 引擎直跑 | `python -m debate`（CLI）与 Web 共用同一引擎 | CLI 另写一套流程逻辑 |

---

## 3. 命名与时间约定

### 3.1 命名

| 对象 | 规则 | 示例 |
|---|---|---|
| DB 字段 / API JSON 字段 | `snake_case`（两端一致，零映射） | `team_total_pro`、`after_seq` |
| Python | 模块/函数 `snake_case`，类 `PascalCase` | `debate_runner.py` / `DebateState` |
| TypeScript | 变量/函数 `camelCase`；**接口字段跟随后端 JSON 保持 snake_case** | `interface Speech { team_total_pro: number }` |
| React | 组件 `PascalCase`，hook `useXxx`，store `XxxStore` | `SpeechCard` / `useDebateStream` |
| role_id | `pro-1..4`、`con-1..4`、`chair`、`timekeeper`、`judge-logic`、`judge-content`、`judge-expression` | 不可自创第五种前缀 |
| 枚举 | `camp`: pro/con/neutral；`format`: full/lite；`status`: pending/running/finished/failed/stopped | 值集封闭，见 SPEC §1 |
| 文件 | 后端 `src/debate/**`，前端 `frontend/src/**`；导出固定名 `transcript.md` / `scorecard.md` / `report.html` | CLI 输出目录 `debate/<YYYY-MM-DD-HHMM>/` |
| commit | conventional：`feat/fix/docs/refactor/test/chore:` | — |

### 3.2 时间约定

| 规则 | 内容 |
|---|---|
| 时区 | 全链路统一**东八区（Asia/Shanghai）**：DB DATETIME 存本地东八区值，API 输出 ISO 8601 带偏移 `2026-10-24T09:30:00+08:00`；禁止混用 UTC |
| 两套"时钟" | **模拟时长**（环节字数预算，与墙钟无关）与**真实时间**（`created_at`/`started_at`）严格分离，变量与文档不得混用 |
| 顺序标识 | `speeches.seq` 场内从 1 单调递增；SSE 事件 id：speech 事件 = 其 seq，其余事件全局自增 |
| 配额口径 | "每日"以**东八区自然日**切分 |

---

## 4. 输出格式要求（硬性，机器校验优先）

| # | 格式约束 |
|---|---|
| F1 | API 错误统一 `{"code","message"}`；code 必须出自 SPEC §5.1 错误码表，禁止自造码 |
| F2 | SSE 事件固定 10 种（phase_start / speech_delta / speech / timer / interrupt / phase_end / judging / verdict / status / error），载荷字段不可增删；speech_delta 仅直播期存在、不落库不重放（v2.1 修订：原 9 种 + speech_delta）；心跳 `: ping` 每 15s；终态后必须关闭流 |
| F3 | 评委输出必须过 Pydantic 校验（SPEC §4.7 schema）；highlights/weaknesses 每侧 2~3 条；不合格重试 2 次 → 弃权卡，禁止无校验放行 |
| F4 | 发言正文为**纯文本**：`[打断]` 标记入库前剥离；前端禁用 `dangerouslySetInnerHTML` |
| F5 | 实录行格式 `**{阵营·辩位}**：{text}`，旁注独立斜体行 `*（{side_note}）*`，截断发言尾部加 `（被计时截断）` |
| F6 | 导出 HTML 单文件内联 CSS、零外部请求、零 JS；配色 token 按 SPEC §8 |
| F7 | 时间字段一律 ISO 8601 +08:00；token 用量为整数统计，进 `usage_json` |
| F8 | LLM 占位降级文本固定 `[系统] 该辩手超时未发言` + 旁注 `LLM 降级`（不可改文案，前端有断言） |

---

## 5. 越界怎么办（违规处置协议）

| # | 情形 | 处置 |
|---|---|---|
| V1 | 需求未覆盖 / 文档冲突 | **停**：不实现该项；记入 SPEC 附录 B 开放问题；向用户确认后再动。禁止自行发明功能"顺手补齐" |
| V2 | 想换技术栈 / 加依赖 | 走 §1.1 依赖纪律；未批准前按锁定清单执行 |
| V3 | 跨了服务边界（如 B2 import） | 评审一律打回；重构归位后才可合并 |
| V4 | 要改表结构 | 只走 Alembic 迁移；禁止手改库、禁止代码里 `CREATE TABLE` |
| V5 | 要改 Prompt | 只改 `roles/ rules/ topics/` 文档；五段模板缺段由 loader fail-fast，禁止在代码里"临时补"提示词 |
| V6 | LLM 输出不合格式 / 连续降级 | 按 SPEC §4.7 / §4.9 执行重试→弃权→熔断；禁止放宽校验"让它过" |
| V7 | 超时 / 截断与内容完整性冲突 | 宁可截断 + 旁注，禁止放宽字数预算或延时等待 |
| V8 | 端口 / 环境冲突 | 走环境覆盖（§1.2），改动记录进 README 运行说明 |
| V9 | 约束本身不合理 | 允许改约束，但必须**先改文档后改代码**：修订 SPEC → 同步本卡 → 再实现 |
| V10 | 所有已发生的越界决定 | 回写记录（改了哪条约束、为什么），保持文档与实现一致——文档滞后 = 新一轮越界 |

### 提交前自检清单

- [ ] 无新增依赖（或已走 V2 流程）
- [ ] 无跨边界 import（对照 §2 九条）
- [ ] 错误码、SSE 事件、枚举值均在封闭集合内
- [ ] 时间字段 ISO 8601 +08:00；seq / 事件 id 单调
- [ ] Prompt 无硬编码；表结构变更带 Alembic 迁移
- [ ] 前端纯文本渲染；导出产物符合 F5/F6
- [ ] LLM 相关改动未绕过校验 / 降级 / 熔断路径
