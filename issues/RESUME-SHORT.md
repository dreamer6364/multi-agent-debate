# 简历短版（AI 应用岗）与数字弹药

> 用途：面试自我介绍 + 简历「项目经历」栏；每条可自证（文件锚点 / 命令 / 报告）。
> 口径纪律：先讲取舍和结果，再讲技术；不吹覆盖、不报没测过的数字。

---

## 30 秒项目介绍（开口就用）

"我做的是一个**多 Agent 对抗辩论模拟平台**：后端 FastAPI + LangGraph，13 个赛制环节由确定性状态机编排，每个辩手节点内有 Function Calling 工具循环（本地检索 / 联网 / 论证自检），三评委并发评分。我负责把 LLM 的不确定性关进工程笼子——计时、打断、判胜由代码保证，模型只在节点内发挥。项目从 0 到 1，跑通真模型端到端：**23 条发言、三维评分全出分**，后端 304 项测试、覆盖率 85%。"

## 简历「项目经历」短版（4 条，AI 应用岗）

1. **多 Agent 编排与确定性护栏**：LangGraph 13 环节 DAG + 节点内工具循环；计时/打断/判胜由代码裁决（非 LLM），避免赛制被模型改写 → `engine/nodes/phases.py`、`tools/agent_tools.py`、CONSTRAINTS B4。
2. **Prompt 效果评测体系**：10 条 golden set + LLM-as-judge 三维评分（立场坚守/引用诚信/论证质量，双方 0~10），改前改后跑分对比落盘 → `backend/eval/`、16 项单测、`eval/reports/smoke-fixed.json`（真实 GLM 冒烟：23 发言、31 调用、139k prompt tokens）。
3. **生产级韧性**：内容过滤误拦证据卡书名导致整场熔断——七支探针逐层定位 → 资产改写 + 网关 400 fail-fast + 引擎去证据回退三层修复；单场 token 硬上限复用熔断终态 → `issues/ISS-015-*.md`、`issues/ISS-014 A-4`。
4. **可观测性与质量门禁**：trace_id 贯通 SSE 与每次 LLM 调用日志（tokens/延迟/失败重试），CI 三件套 + 覆盖率 85% 基线、Redis 旁路缓存、Alembic 进部署链 → `engine/llm/observability.py`、`.github/workflows/ci.yml`。

---

## 3 个 STAR 故事骨架（每个 60~90 秒，按追问展开）

### STAR 1：内容过滤误拦熔断（最能体现定位能力）
- **S**：A-1 真模型冒烟，辩手连续 5 次降级熔断、比赛 100% 失败，Web 端同引擎同挂。
- **T**：不改引擎代码先定位根因（避免头疼医头）。
- **A**：写 7 支探针逐层剥除——定位到是知识卡里《监控资本主义时代》**书名文本**命中 GLM 黑名单（裸词/作者名全过、书名号包着也拦、秒拒硬匹配）；三层修复：资产改写 + 网关 400 类 fail-fast（省白烧）+ 引擎去证据回退（根治，黑名单会演进）。
- **R**：冒烟从 failed → finished、23 发言全出分；每次内容过滤从 4 枪+退避降到 1 枪即抛；未来任何注入源误拦只损失证据不熔断。

### STAR 2：Prompt 评测体系（AI 岗核心）
- **S**：面试官视角发现"提示词文件化只保证结构、不保证质量"，改 prompt 无回归手段。
- **T**：建一套可复现、可对比、可落盘的效果评测。
- **A**：10 条 golden set（3 条与内置辩题逐字段同步、单测锁死防漂移，7 条新领域防过拟合）+ LLM-as-judge 三维评分（`temperature=0.1` 降随机、围栏剥离/纠错重试/降级语义），CLI `--label` 前后跑分对比。
- **R**：真实 GLM 冒烟出分（立场 7/8、引用 8/9、论证 6/8），报告自含全场实录；"改了一个词怎么证明没变差"从无解变成一条命令。

### STAR 3：日志 + trace_id 排查复读（可观测性）
- **S**：弱模型偶发把发言输出成 JSON / 集体复读，只有 SSE 实录、无调用级视图。
- **T**：回答"辩手突然集体复读，你的排查路径"。
- **A**：每场一个 trace_id 贯穿 SSE 事件与每次 LLM 调用日志（tier/tokens/延迟/失败 attempt，不落 prompt 全文护隐私）；配合 A-1 评测报告分离"发生了什么"与"好不好"。
- **R**：按 trace_id 过滤即可判定是模型输出异常还是降级占位，无需翻实录猜；6 项回归锁定。

---

## 数字弹药速查（被追问细节时给）

| 指标 | 数字 | 自证 |
|---|---|---|
| 后端测试 | **304 passed**、ruff `src tests eval` 0 | `cd backend; pytest -q -n 4` |
| 覆盖率基线 | **85%**（4474 语句/680 未覆盖，taken @298 passed） | `pytest --cov=src/debate --cov-report=term-missing` |
| 前端测试 | Vitest 9 项 + Playwright 1 条主链路冒烟 | `npm test` / `frontend/e2e/arena.spec.ts` |
| 真实 LLM 冒烟 | 23 发言 / 31 调用 / 139,134 prompt tokens / 三维全出分 | `backend/eval/reports/smoke-fixed.json` |
| 角色 / 证据卡 / golden set | 13 角色 / 20 张卡 / 10 条辩题 | `roles/` / `knowledge/` / `eval/golden_set.jsonl` |
| 已关闭 issue | ISS-001~015；ISS-014 15 条中 13 条已修/已备，仅剩 B-2（docker 环境）、B-4（flake 根治） | `issues/README.md` |
