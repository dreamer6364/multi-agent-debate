# 多 Agent 对抗辩论模拟平台 · 测试计划与用例规格

| 文档信息 | 内容 |
|---|---|
| 文档版本 | v1.0（2026-10-24） |
| 关联文档 | [PRD.md](PRD.md)（验收来源 §10）· [ARCHITECTURE.md](ARCHITECTURE.md) §14 · [SPEC.md](SPEC.md) §11 · [CONSTRAINTS.md](CONSTRAINTS.md) |
| 定位 | 测试基础设施规格 + 分层用例清单 + 验收追踪矩阵。用例与文档条款冲突时以 SPEC 为准并回改本计划 |
| 文档状态 | 待评审 |

---

## 1. 测试策略与金字塔

```
            ┌──────────────┐
            │ E2E 验收      │  Playwright(Web) + 真模型冒烟(人工)     少而慢
          ┌─┴──────────────┴─┐
          │ API / SSE 集成    │  httpx ASGI + FakeLLM                  中
        ┌─┴──────────────────┴─┐
        │ 引擎单元测试           │  pytest + FakeLLM + 确定性断言          多而快
      ┌─┴──────────────────────┴─┐
      │ 前端组件/Hook 测试         │  Vitest + Testing Library + mock SSE   多而快
      └──────────────────────────┘
```

原则：

1. **一切含 LLM 的路径默认用 FakeLLM 测**，真模型只出现在 E2E 冒烟（成本与不确定性控制）；
2. 确定性层（计时/打断/胜负/渲染）**禁止 mock 内部实现**，只断言公共行为；
3. 每条用例必须能追溯到文档条款（SPEC / PRD AC / CONSTRAINTS），无追溯的用例删除。

---

## 2. 测试基础设施规格

### 2.1 FakeLLM（`tests/fakes/llm.py`，最关键基建）

实现与 `llm/gateway.py` 相同的调用签名，三种模式：

| 模式 | 行为 | 用途 |
|---|---|---|
| `scripted` | 按 **剧本 fixture** 回放：队列式弹出预置发言（含 speaker、text、side_notes 预设标记）；剧本耗尽抛 `ScriptExhausted` | 图流程、SSE、E2E-Web 全链路 |
| `echo` | 复述任务指令中的预算与角色（可控字数：`"{repeat:660}"` 占位符展开为 660 字） | 计时、截断、digest 边界 |
| `fault` | 故障注入：`timeout` / `http_500` / `malformed_json` / `oversized`（超预算 3 倍）/ `abuse`（命中词表）/ `interrupt_spam`（3 次打断）/ `meta`（"作为AI…"） | 降级、熔断、违规检测 |

**剧本 fixture**（`tests/fakes/script_lite.json`）：一场完整 lite 比赛（5 环节约 20 条发言），内含设计好的钩子——一处 `[打断]`、一处命中词表、一处超预算长文、评委输出含一对平票（触发三级决胜）。全项目共用，保证断言确定性。

### 2.2 测试数据库与环境

| 项 | 规格 |
|---|---|
| 测试库 | MySQL `mad_test`（compose 内 service），session 级 Alembic 建表，**用例级 TRUNCATE**（不用事务回滚——引擎是异步后台任务，须可见提交）。**v2.1 修订**：开发环境无 MySQL 凭据时回落 SQLite(aiosqlite, tmp 文件)，SQLModel 可移植，生产路径不变 |
| 环境变量 | `MYSQL_URL_TEST`、`JWT_SECRET=test`、`FAKE_LLM=1`（后端启动时以 FakeLLM 替换 gateway） |
| SSE 直播测试 | **必须走真实 uvicorn 线程服务器**（`LiveServer`，v2.1 增补）——httpx `ASGITransport` 会缓冲完整响应体，阻塞中的比赛一个字节都吐不出来，流式语义在测试传输层失真 |
| 种子 | 内置辩题 3 条（PRD §7）由迁移写入；测试用户 `alice`/`bob`（密码 `password8`）由 fixture 创建 |
| 时间 | 注入可控时钟 fixture（自由推进），测配额"自然日切分" |
| ASGI | `httpx.AsyncClient(transport=ASGITransport(app))`；SSE 用流式读取 + 事件解析器 fixture |

### 2.3 前端测试基建

`mock-event-source`（可手动 push 事件的 EventSource 替身）、msw 拦截 REST；Playwright 走 `FAKE_LLM=1` 后端。

---

## 3. 用例编号体系与追踪

| 前缀 | 层 | 前缀 | 层 |
|---|---|---|---|
| `UT-*` | 引擎单元 | `IT-*` | API 集成 |
| `SSE-*` | SSE 协议 | `FE-*` | 前端 |
| `E2E-*` | 端到端 | `FT-*` | 故障注入 |
| `PF-*` | 非功能 | `AC-*` | 验收标准（映射用） |

---

## 4. 引擎单元测试（`backend/tests/unit/`）

### 4.1 计时 UT-TIMER（对照 SPEC §4.2）

| ID | 操作 | 期望 |
|---|---|---|
| UT-TIMER-01 | 3 分钟环节预算计算 | `budget = 660` 字 |
| UT-TIMER-02 | 发言消耗至剩余 110 字 | 恰插入 1 条"还剩 30 秒"旁注，再次触发不再插 |
| UT-TIMER-03 | echo 模式输出超预算长文 | 截断至完整句边界 + "时间到"旁注，`word_count ≤ budget` |
| UT-TIMER-04 | 自由辩：反方池先耗尽 | 旁注"反方时间用尽"，正方连讲至耗尽后环节结束 |
| UT-TIMER-05 | 自由辩单次 turn cap | 单条发言 >110 字被截断且不消耗对方池 |

### 4.2 打断 UT-INTERRUPT（SPEC §4.3）

| ID | 操作 | 期望 |
|---|---|---|
| UT-INT-01 | 回答含 `[打断]请正面回答` | 标记剥离入正文，触发打断事件 |
| UT-INT-02 | 全角【打断】 | 等效识别 |
| UT-INT-03 | 同一回答第 3 次打断 | 标记不生效 + 旁注"打断违规（超限）" |
| UT-INT-04 | phase 5 出现 `[打断]` | 无效（打断仅限 phase 3） |

### 4.3 上下文 UT-CONTEXT（SPEC §4.4 + AGENT_CONSTRAINTS §6）

| ID | 操作 | 期望 |
|---|---|---|
| UT-CTX-01 | 组装辩手消息 | 五段顺序固定：system(conduct+role) → 立场 → digest → 最近 6 条 → 环节指令 |
| UT-CTX-02 | 正方辩手上下文 | **不含** `con_points`（反方同理），评委上下文含双方 |
| UT-CTX-03 | 超过 max_input_tokens | 按 4→2→3 顺序裁剪（先砍最近原文） |
| UT-CTX-04 | 四辩/评委注入 | 含全场要点清单 + 摘录池（每环节 ≤4 条原句）；四辩不含"最近 6 条" |

### 4.4 digest UT-DIGEST（SPEC §4.5）

| ID | 操作 | 期望 |
|---|---|---|
| UT-DIG-01 | 每环节末更新 | 五栏结构完整 |
| UT-DIG-02 | 合并后超长 | 压缩旧栏，"交锋点/谬误指控"两栏永不丢 |
| UT-DIG-03 | 摘录池 | 每环节 ≤4 条，含 speaker 归属 |

### 4.5 图与流程 UT-GRAPH（SPEC §4.1）

| ID | 操作 | 期望 |
|---|---|---|
| UT-GRF-01 | full 剧本跑完 | 节点顺序 = 10 环节；发言序列与 SPEC §4.1 表逐行一致 |
| UT-GRF-02 | lite 剧本跑完 | 仅执行 phase 2/4/7/8/9；attack 维度 15 分并入 rebuttal_cross |
| UT-GRF-03 | stop 信号 | 引擎在当前节点边界退出，status=stopped，无半条发言入库 |

### 4.6 评分与胜负 UT-SCORE（SPEC §4.7/§4.8）

| ID | 操作 | 期望 |
|---|---|---|
| UT-SCO-01 | 合法评委 JSON | 六维求和、两队均值四舍五入 |
| UT-SCO-02 | malformed_json ×3 | 弃权卡：分数全 0、不计票、标注"输出异常弃权" |
| UT-SCO-03 | 2:1 投票 | winner=多数方 |
| UT-SCO-04 | 平票（剧本预设） | 比团体总分；再平按 best_speaker 众数所在队 |
| UT-SCO-05 | highlights 条数 1 条 | 校验失败 → 重试；重试仍不合格 → 弃权 |

### 4.7 加载与守则 UT-LOAD（AGENT_CONSTRAINTS §8 / SPEC §7）

| ID | 操作 | 期望 |
|---|---|---|
| UT-LOD-01 | 角色文件缺"禁止事项"段 | `PromptAssetError` fail-fast |
| UT-LOD-02 | 一辩文件删去"判准"关键词 | fail-fast（义务关键词校验） |
| UT-LOD-03 | timekeeper 加载 | 不构造 LLM 调用，旁注仅代码生成 |
| UT-LOD-04 | system prompt 拼接顺序 | conduct 全文在前、角色全文在后 |

### 4.8 守则与违规 UT-CONDUCT（SPEC §4.6 / AGENT_CONSTRAINTS §7）

| ID | 操作 | 期望 |
|---|---|---|
| UT-CND-01 | 发言命中 abuse 词表 | 旁注"违规：人身攻击" |
| UT-CND-02 | `fault:meta` 输出"作为AI" | 剥离该句 + 旁注"违规：跳出角色" |
| UT-CND-03 | `#` 开头行 / 代码块 | 剥离，正文保留 |
| UT-CND-04 | 同一问题第 3 次追问未回应 | 旁注"回避追问" |

### 4.9 渲染 UT-RENDER（SPEC §8 / CONSTRAINTS F5/F6）

| ID | 操作 | 期望 |
|---|---|---|
| UT-RND-01 | transcript.md 快照 | 元信息块 + `## 【环节 N｜…】` + `**{阵营·辩位}**：` + 斜体旁注行 |
| UT-RND-02 | scorecard.md 快照 | 三评委行 + 胜负说明 + 最佳辩手 + 用量表 |
| UT-RND-03 | report.html | 无 `<script>`、无外部 `http(s)` 引用、含五色配色 token、六维 SVG 雷达图 |
| UT-RND-04 | 含截断与违规旁注的场 | 快照含"（被计时截断）"与旁注行 |

---

## 5. API 集成测试（`backend/tests/api/`，FakeLLM 后端）

### 5.1 认证 IT-AUTH（SPEC §5.2）

| ID | 操作 | 期望 |
|---|---|---|
| IT-AUT-01 | 注册 alice | 201；重复注册 → 409 USERNAME_TAKEN |
| IT-AUT-02 | 密码 7 位 | 422 VALIDATION_ERROR |
| IT-AUT-03 | 登录成功 | 200 + `Set-Cookie: access_token; HttpOnly; SameSite=Lax`；错误密码 → 401 AUTH_INVALID |
| IT-AUT-04 | 未登录访问 /api/topics | 401 AUTH_REQUIRED |
| IT-AUT-05 | /api/auth/me | 含 `quota.daily_used / daily_limit` |

### 5.2 辩题 IT-TOPIC

| ID | 操作 | 期望 |
|---|---|---|
| IT-TOP-01 | 列表 | 内置 3 条 + 自己的；看不到 bob 的 |
| IT-TOP-02 | 删内置辩题 | 403 FORBIDDEN |
| IT-TOP-03 | 删他人辩题 | 404（不泄露存在性） |
| IT-TOP-04 | 删已被比赛引用的辩题 | 409 TOPIC_IN_USE |

### 5.3 比赛 IT-DEB（SPEC §5.2 / CONSTRAINTS B7）

| ID | 操作 | 期望 |
|---|---|---|
| IT-DEB-01 | topic_id 发起 | 202 pending →（FakeLLM）终态 finished；`winner/team_totals/usage_json` 落库 |
| IT-DEB-02 | custom_topic 发起 | 自定义辩题先落 topics（owner=当前用户）再关联 |
| IT-DEB-03 | GET bob 的比赛 | 404 |
| IT-DEB-04 | alice 已有 running 再发起 | 429 QUOTA_CONCURRENT |
| IT-DEB-05 | 当日第 6 场（时钟推进） | 429 QUOTA_DAILY；次日恢复 |
| IT-DEB-06 | stop running / stop finished | 200 stopped / 409 DEBATE_NOT_RUNNING |
| IT-DEB-07 | 赛后导出 md/html；进行中导出 | 200（md=text/markdown）/ 409 EXPORT_NOT_READY |

### 5.4 发言 IT-SPCH

| ID | 操作 | 期望 |
|---|---|---|
| IT-SPC-01 | `after_seq=0` 全量、`after_seq=N` 增量 | seq 升序、无重叠 |
| IT-SPC-02 | 剧本预设的违规/截断发言 | side_notes 与 interrupted 字段落库正确 |

---

## 6. SSE 协议测试（`backend/tests/sse/`，SPEC §6）

| ID | 操作 | 期望 |
|---|---|---|
| SSE-01 | 完整消费一场 | 事件序列 = `phase_start → (speech/timer/interrupt)* → phase_end`×N → `judging → verdict → status(finished)` 后服务端关闭 |
| SSE-02 | speech 事件 | `id == seq`，字段与 SPEC §6.2 完全一致（多余/缺失字段即 fail） |
| SSE-03 | 中途断开，`?after_seq=N` 重连 | 先重放 seq>N 全部 speech，再接实时流，无丢失、无重复 |
| SSE-04 | 心跳 | ≤15s 收到 `: ping`（测试时钟加速验证） |
| SSE-05 | failed 场 | 收到 `status{failed, error}` 后关闭 |
| SSE-06 | 10 并发订阅同一场 | 每个连接收到完整等价事件流 |

---

## 7. 前端测试（`frontend/tests/`，Vitest + Testing Library）

| ID | 操作 | 期望 |
|---|---|---|
| FE-STR-01 | hook 收到 speech 事件 | store 追加且按 seq 去重排序 |
| FE-STR-02 | EventSource 连续 error ×3 | 降级 5s 轮询 `speeches?after_seq` |
| FE-STR-03 | 收到终态 status | 关闭连接；页面展示胜方横幅 |
| FE-STR-04 | 页面刷新 | 先 REST 补齐历史再续订 SSE，衔接处无缺漏 |
| FE-CMP-01 | SpeechCard 渲染含 `<script>` 的发言文本 | 纯文本转义，脚本不执行（CONSTRAINTS F4） |
| FE-CMP-02 | SpeechCard 旁注/打断徽标 | 按 side_notes/interrupted 正确显示 |
| FE-CMP-03 | PhaseTimeline | full 13 点 / lite 5 点，当前高亮 |
| FE-CMP-04 | ScoreBoard | 六维雷达双系列 + 总分柱状 + 票型 |
| FE-CMP-05 | VerdictBanner 平票决胜 | 显示"按团体总分/最佳辩手决胜"路径说明 |

---

## 8. E2E 测试

| ID | 方式 | 范围 | 准出 |
|---|---|---|---|
| E2E-WEB-01 | Playwright + `FAKE_LLM=1` 后端 | 注册 → 登录 → 建辩题 → 发起 lite → 直播页出现发言流与旁注徽标 → 终态横幅与雷达图 → 归档回看 → 导出按钮可下载 | 全程无 JS 报错，断言关键节点出现 |
| E2E-LITE-01 | 真模型（廉价档）lite 一场 | 人工核对 PRD §10 中 3/4/5/7/8/9 | 人工签收，记录到验收报告 |
| E2E-FULL-01 | 真模型 full 一场 | PRD §10 全量 + SPEC §4.1 环节序列 | 人工签收 |

---

## 9. 故障注入测试（`fault` 模式）

| ID | 注入 | 期望（对照 SPEC §4.9 / §7） |
|---|---|---|
| FT-LLM-01 | 连续 timeout | 重试 3 次 → 占位发言 `[系统] 该辩手超时未发言` + 旁注"LLM 降级"，流程继续 |
| FT-LLM-02 | 连续 3 次降级 | 引擎中止，status=failed，error 落库，SSE 收到 failed |
| FT-LLM-03 | 评委 malformed_json ×3 | 弃权卡，胜负判定跳过其票 |
| FT-LLM-04 | oversized 输出 | 截断 + "时间到"旁注，word_count ≤ budget |
| FT-DB-01 | 引擎写库异常 | 任务中止 → status=failed；恢复后可从归档页看到 error 详情 |
| FT-SSE-01 | 客户端半开连接 | 心跳超时后服务端清理订阅，无句柄泄漏 |

---

## 10. 非功能测试（交付前执行一轮）

| ID | 场景 | 指标 |
|---|---|---|
| PF-01 | 10 SSE 订阅 + 1 场 running（FakeLLM 高频出稿） | 发言落库到客户端可见 p95 < 2s |
| PF-02 | full 剧本场用量统计 | usage_json 总量 ≤ 300k tokens（真模型冒烟时复核） |
| PF-03 | bcrypt 登录 + 100 次 /me | p95 < 100ms（缓存 JWT 解码后） |

---

## 11. 验收追踪矩阵（AC → 用例）

| AC | 内容（PRD §10 + 附录 A） | 追溯用例 |
|---|---|---|
| AC-01 | 完整赛制 13 环节走完无遗漏错序 | UT-GRF-01、E2E-FULL-01 |
| AC-02 | 8 辩手立场稳定无漂移 | UT-CND-02、E2E 人工核对 |
| AC-03 | 发言引用对方原话/队友论点，无自说自话 | UT-CTX-01、E2E 人工核对 |
| AC-04 | 打断仅质询环节且有理由 | UT-INT-01~04 |
| AC-05 | 每环节计时提醒出现 | UT-TIMER-02/03 |
| AC-06 | 3 评委独立分数/投票/亮点不足 | UT-SCO-01、IT-DEB-01 |
| AC-07 | 点评引用具体发言 | UT-SCO-05（引号强制）+ UT-DIG-03 |
| AC-08 | 实录与评分表格式合规 | UT-RND-01/02 |
| AC-09 | 胜负判定符合三级规则 | UT-SCO-03/04 |
| AC-10 | SSE 断线重连不丢发言 | SSE-03、FE-STR-04 |
| AC-11 | 未登录/非属主不可见他人比赛 | IT-AUT-04、IT-DEB-03 |
| AC-12 | 配额拦截生效 | IT-DEB-04/05 |

---

## 12. 准入 / 准出标准

| 项 | 标准 |
|---|---|
| 准入 | 功能代码过 CONSTRAINTS 自检清单；文档无未决 V1 类开放问题 |
| 单元/集成 | 通过率 100%，P0/P1 缺陷为 0 |
| 覆盖率 | `engine/` ≥ 85%、`api/`+`services/` ≥ 80%、前端语句 ≥ 70%（vitest --coverage） |
| 准出 | AC-01~AC-12 全部有对应用例通过；E2E-LITE/FULL 人工签收；FT/PF 全过 |

---

## 13. CI 集成（预留）

| 流水线 | 触发 | 内容 |
|---|---|---|
| `backend-ci` | push/PR | uv sync → ruff → pytest（MySQL service 容器） |
| `frontend-ci` | push/PR | npm ci → eslint → vitest --coverage |
| `e2e` | nightly | compose 起 mysql+backend(FAKE_LLM=1) → Playwright E2E-WEB-01 |

> 项目当前未初始化 git，CI 为计划项；本地开发期以 `pytest && npm test` 手动门槛替代。

---

## 14. 缺陷分级

| 级别 | 定义 | 示例 |
|---|---|---|
| P0 | 流程中断 / 数据错乱 / 权限泄漏 | 比赛卡死、他人比赛可见、seq 断裂 |
| P1 | 违反封闭规格 | 错误码自造、SSE 载荷字段增删、旁注文案偏差 |
| P2 | 功能可用但体验受损 | 截断不在完整句边界、雷达图数据错位 |
| P3 | 打磨项 | 文案、间距、日志冗余 |

P0/P1 修复必须补对应回归用例并更新本计划。
