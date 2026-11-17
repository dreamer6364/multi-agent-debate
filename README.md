# 多 Agent 对抗辩论模拟平台（Multi-Agent Debate）

用多 Agent 模拟一场完整的辩论赛：两支四人队伍围绕同一辩题对抗博弈，配主席、计时员、三位各有侧重的评委，按真实赛制走完 13 个环节，通过 SSE 实时直播到网页，赛后归档入库并可导出 Markdown / HTML 报告。

**Elenchus 论证分析（可选，默认开启）**：逻辑评委点评前自动对双方核心论点做谬误检测（诉诸权威/人身攻击/滑坡等 57 类），结果作为结构化参考注入。源码已随库（`backend/third_party/elenchus`，GitHub 被墙也无需联网），启用安装：`cd backend && uv pip install "./third_party/elenchus[mcp]" "mcp>=1.2,<2"`；未安装自动静默降级。两种模式：`inproc`（进程内直调，默认稳定）/ `mcp`（stdio JSON-RPC 子进程，真 MCP 协议路径）。

**联网搜索（可选）**：辩手开赛时自动检索网络资料并入论据。三种引擎按需启用（`backend/.env`）：
`SEARCH_PROVIDER=zhipu`（用现有智谱 key，需搜索资源包）｜`SEARCH_PROVIDER=tavily` + `TAVILY_API_KEY=tvly-xxx`（[tavily.com](https://tavily.com) 免费注册 1000 次/月）｜`SEARCH_PROVIDER=ddgs`（零 key，需 `pip install ddgs`，海外网络）。搜索失败自动回落本地证据卡，不影响比赛。

**文档**：[PRD.md](PRD.md)（需求）· [ARCHITECTURE.md](ARCHITECTURE.md)（架构）· [SPEC.md](SPEC.md)（详细规格）· [CONSTRAINTS.md](CONSTRAINTS.md)（硬约束卡）· [AGENT_CONSTRAINTS.md](AGENT_CONSTRAINTS.md)（Agent 行为约束）· [TEST_PLAN.md](TEST_PLAN.md)（测试计划）

## 技术栈

| 层 | 选型 |
|---|---|
| 引擎 | Python 3.11+ · LangGraph · OpenAI 兼容协议（GLM/DeepSeek/Kimi/OpenAI 可切换） · RAG 证据检索（BM25 + 可选联网搜索） |
| 后端 | FastAPI · SQLModel · MySQL 8（开发/测试可回落 SQLite） · SSE · Redis 可选只读缓存（`REDIS_URL` 未配置自动回落进程内，故障降级直读 DB） |
| 前端 | React 18 + Vite + TypeScript + Ant Design 5 · zustand · TanStack Query |
| 交付 | Docker Compose（backend + mysql + nginx） |

## 一键启动（推荐）

```bat
start.bat          真模型模式：后端 8000 + 前端 5173（读 backend\.env 的 GLM 配置），自动打开浏览器
start.bat demo     演示模式：FakeLLM 剧本回放（零 token），后端 8010 + 前端 5174
start.bat stop     停止全部服务并清理端口
```

脚本会自动：检查 MySQL 服务 → 清理端口残留 → 启动后端并等待健康检查 → 启动前端（首次自动装依赖）→ 打开浏览器。两个服务窗口保持打开即可；关闭它们或运行 `start.bat stop` 即停止。

## 快速开始（手动开发模式）

```bash
# 1) 后端
cd backend
uv sync                                # 安装依赖
copy .env.example .env                 # 填入 OPENAI_API_KEY / OPENAI_BASE_URL / MODEL_*
.venv/Scripts/python -m uvicorn debate.api.app:create_app --factory --port 8000
#   未配置 MySQL 时自动使用本地 SQLite（debate-dev.db）；生产请配置 MYSQL_URL

# 2) 前端
cd frontend
npm install
npm run dev                            # http://localhost:5173（/api 已代理到 8000）
```

**零 token 联调档**（不调真实模型，回放内置剧本）：

```bash
cd backend
.venv/Scripts/python scripts/dev_uvicorn.py 8000      # FakeLLM Web 服务器
```

**CLI 直跑**（无 Web，出 Markdown 文件）：

```bash
cd backend
.venv/Scripts/python -m debate check                       # 配置与 Prompt 资产自检
.venv/Scripts/python -m debate run --topic topic-01 --format lite --fake
```

## 快速开始（生产交付）

```bash
# 项目根目录准备 .env：OPENAI_API_KEY / OPENAI_BASE_URL / MODEL_DEBATER / MODEL_OFFICIAL / JWT_SECRET
docker compose up -d
# 访问 http://localhost（nginx 托管前端 + 反代 /api，SSE 已关缓冲）
```

后端容器启动时先执行 `alembic upgrade head` 再起 uvicorn（ISS-014 B-3）：
表结构变更只走 Alembic 迁移（CONSTRAINTS V4），空库由迁移完成全部建表，
历史 `create_all` 引导的旧库迁移已幂等兼容（版本对齐，不重建表）。

## 测试

```bash
cd backend  && .venv/Scripts/python -m pytest -q    # 298 项：引擎/评分/计时/打断/SSE/API/评测
cd frontend && npm test                              # 9 项：直播 Hook/组件/XSS 防护
```

## 评测（Prompt 效果回归，ISS-014 A-1）

```bash
cd backend
.venv/Scripts/python -m eval.run_eval --dry-run                     # 只验资产（golden set 10 条 + 13 角色 Prompt），不出网
.venv/Scripts/python -m eval.run_eval --topics g01 --format lite --label smoke   # 单题真实 LLM 冒烟
.venv/Scripts/python -m eval.run_eval --limit 5 --label before      # prompt 改动前基线；改后再跑 --label after 对比
```

报告落盘 `backend/eval/reports/<label>.json`：LLM-as-judge 三维评分（立场坚守 / 引用诚信 / 论证质量，双方各 0~10）
+ 全场实录 + token 用量；`--label` 分别落盘即成"改前/改后"对比，回答"prompt 改了怎么证明没变差"。

## 一场比赛怎么跑

1. 注册登录 → 辩题库选辩题（或自建，含正反破题要点）→ 选赛制（完整 13 环节 / 精简 5 环节）与评委数 → 发起；
2. 直播页实时观看：发言流按角色配色、计时提醒与打断徽标、六节点时间线点亮进度；
3. 赛后：胜方横幅、三位评委独立评分与亮点/不足点评、六维雷达图、按用户隔离的历史归档、导出 MD 实录与 HTML 精排报告。

## 角色阵容（13 个 Agent 位）

正反方各四辩（立论/驳论/攻辩/总结）+ 主席 + 计时员（确定性代码，非 LLM）+ 三评委（逻辑/内容/表达，结构化打分，并行点评）。角色行为约束见 [AGENT_CONSTRAINTS.md](AGENT_CONSTRAINTS.md)，Prompt 源文件在 `backend/roles/`、`backend/rules/`、`backend/topics/`（文档即代码，运行时加载并 fail-fast 校验）。

## 验收状态（PRD §10 + 全栈补充）

| 验收项 | 状态 | 依据 |
|---|---|---|
| 完整赛制 13 环节走完无遗漏错序 | ✅ | UT-GRF-01 + E2E-FULL-01（真模型 full 实跑：13 环节 finished，三评委零弃权） |
| 辩手立场稳定无漂移 | ✅ | 词表/元话语检测 + 评委 rubric |
| 发言引用交锋、无自说自话 | ✅ | 上下文注入五段式 + 评委查证 |
| 打断仅质询环节且有理由 | ✅ | UT-INT-01~04 |
| 每环节计时提醒 | ✅ | UT-TIMER |
| 3 评委独立打分/投票/亮点不足 | ✅ | IT-DEB + SSE verdict 事件 |
| 点评引用具体发言 | ✅ | JSON schema 引号强制 |
| 实录与评分表格式合规 | ✅ | UT-RND |
| 三级胜负判定规则 | ✅ | UT-SCO |
| SSE 断线重连不丢发言 | ✅ | SSE-03（after_seq 续传） |
| 他人比赛不可见 | ✅ | IT-DEB-03（404 不泄露存在性） |
| 并发护栏生效（每日不限） | ✅ | IT-DEB-04/05（并发 1 场；v1.3 删除每日配额） |
| 前端直播页/Hook 行为 | ✅ | Vitest FE-STR/FE-CMP + 真浏览器冒烟 |
| E2E-Web（Playwright）/ 真模型 lite 冒烟 | ✅ | E2E-WEB-01（Edge 通道 9.6s）+ GLM glm-4.5-flash lite 实跑出分 |

## 项目结构

```
backend/   FastAPI + LangGraph 引擎 + SQLModel 持久层 + tests（pytest）
  rules/ roles/ topics/    文档资产 = 运行时 Prompt 源
  eval/                    golden set + LLM-as-judge 三维评测（ISS-014 A-1）
  scripts/dev_uvicorn.py   开发档：FakeLLM Web 服务器（零 token 联调）
frontend/  React + Vite + AntD SPA + tests（Vitest）
docker-compose.yml         backend + mysql + nginx 三容器
PRD / ARCHITECTURE / SPEC / CONSTRAINTS / AGENT_CONSTRAINTS / TEST_PLAN
```
