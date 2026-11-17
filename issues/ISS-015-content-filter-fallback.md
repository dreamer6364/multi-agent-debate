# ISS-015（生产）内容过滤误拦证据卡书名 → 连续降级熔断整场失败，无注入降级回退

**状态**：已修复（3/3，2026-11-17）
**发现日期**：2026-11-17
**发现途径**：ISS-014 A-1 评测冒烟（`python -m eval.run_eval --topics g01 --format lite`）
**严重级**：P0（用户发起比赛直接失败，确定性复现；同引擎影响 Web 端）

---

## 1. 现象与事实链

冒烟两次均在辩手环节熔断：`比赛=failed，error=连续 5 次 LLM 降级，触发熔断`，
judge 照常跑完但因"辩手未发言"给出 0 分。诊断脚本（归档 `issues/evidence/_diag_a1_*.py`）逐层定位：

| 层级 | 结论 | 证据脚本 |
|---|---|---|
| 底层异常 | 辩手调用 `400 code=1301 contentFilter`（role=user，输入侧误拦）；chair/digest/judge 同模型全过 | `_diag_a1_smoke.py` |
| 注入源 | 联网卡=0 张无关；**本地知识卡**是触发源（去证据变体通过） | `_diag_a1_filter_probe.py` |
| 单卡 | 3 张卡中 **card[1]《监控资本主义时代》单卡即命中** | `_diag_a1_card_probe.py` |
| 卡内 | 触发点在**来源行**（卡片名含"监控"通过） | `_diag_a1_phrase_probe.py` / `_diag_a1_phrase2_probe.py` |
| 关键词 | "监控""资本主义""监控资本主义"裸词全过、作者名过；唯 **`监控资本主义时代` 书名文本命中黑名单**（带不带书名号都拦，秒拒 ≈ 关键词硬匹配） | `_diag_a1_token_probe.py` |

**影响面**：
- **生产**：topic-01（AI 依赖）当前 100% 比赛失败——`run_debate` 同引擎，Web 端同样熔断；
- **成本**：`MIN_CALL_INTERVAL=15` 下每次失败 `gateway.complete` 白烧 ~150s（4 连打全被拒仍重试 3 次）；
- **评测**：golden set 首条即挂，A-1 评分链路无法端到端出数。

## 2. 面试官潜台词

"内容过滤是供应商黑盒，你的系统对它的误拦有什么韧性？"——当前答案是**没有**：
单点注入误拦 → 无回退 → 整场熔断。

## 3. 修复分层

| # | 层 | 修复 | 状态 |
|---|---|---|---|
| 1 | **资产层** | `knowledge/evidence-base.md` 来源行改写：`《监控资本主义时代》（肖莎娜·祖博夫）` → `肖莎娜·祖博夫（平台经济批判著作）`（黑名单匹配书名文本，作者直引规避；正文"监控资本主义"概念裸词已验证安全） | ✅ 已修（改写候选经 V1/V2 探针验证：裸书名仍拦、仅作者通过；改写后逐卡复探 P0~P3 全过） |
| 2 | **网关层** | `Gateway` 新增 `ContentFiltered(LLMExhausted)` 与 `_fail_fast` 分类：**contentFilter 400 → ContentFiltered、参数/鉴权类 4xx（异常类名或 `Error code: 400/401/403/404/422` 消息形态）→ LLMExhausted**，均首打即抛、不进 3 次重试 + 退避；429/5xx/超时仍照旧重试（回归测试锁定）。识别漏了仍走满重试（与修复前一致），不引入新风险 | ✅ 已修（`gateway.py` complete/stream 双路径；测试 6 项：fail-fast 不打第二枪不烧退避、流式同、类名/消息两形态、429+500 仍重试、子类兼容） |
| 3 | **引擎层** | `phases._ask` 增加 `except ContentFiltered` 分支（先于 `LLMExhausted`）：该次有证据卡 → **去证据重建 messages 用 complete 重试一次**；成功则 `degradation_streak=0` 继续、旁注 `证据降级`，仍失败（或本无证据）才落占位 `[系统] 该辩手发言被内容过滤拦截`（旁注 `LLM 降级`+`内容过滤拦截`，计一次降级）；JSON 纠偏重试在已回退时不再注入已知被拦源。**根治性：未来任何注入源（新知识卡/联网结果）误拦都只损失证据、不熔断整场** | ✅ 已修（`phases.py`；测试 5 项：回退成功/流式回退/无证据即降级/回退再败只计一笔/正常路径零干预；SPEC §4.9 同步） |

**残余风险**：黑名单是供应商黑盒且会演进——其他知识卡书名、其他辩题未逐一探过。
#1 只解除当前已知卡点；#2 让同类拦截不再白烧重试；**#3 已根治**：今后任何注入源误拦
只损失证据（旁注 `证据降级`），不再熔断整场。

## 4. 预估/实际工时

预估：资产 0.1h + 网关 fail-fast 0.5h + 引擎回退 1~1.5h ≈ 2~2.1h。
实际：三层全部完成（含 11 项单测与 SPEC §4.9 同步），未超预估。

## 5. 验证证据

- 单测：`tests/unit/test_gateway.py` 新增 6 项 + `tests/unit/test_iss015_fallback.py` 新增 5 项；全量 pytest **285 passed**（274+11）、`ruff check src tests eval` 0。
- 真实网关验证（GLM 实跑）：`issues/evidence/_diag_a1_failfast_verify.py` —— V1 触发词复现拦截且 fail-fast 1 枪无退避、V2 去证据出稿、V3 完整 `_ask` 旁注"证据降级"且降级计数 0。
- 修复前行为留证：`eval/reports/smoke.json`、`smoke2.json`（熔断失败）+ 七支 `_diag_a1_*.py` 探针（逐层定位）。

## 6. 复现/核验命令

```powershell
# 资产层修复后复现（应全过）
cd backend
.venv/Scripts/python.exe -X utf8 ..\issues\evidence\_diag_a1_card_probe.py   # P1 应 OK

# #2/#3 单元测试（fail-fast + 去证据回退，不出网）
.venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_gateway.py tests/unit/test_iss015_fallback.py -q

# #2/#3 真实网关验证（GLM 实跑，~1min：触发词复现拦截 → fail-fast 1 枪 → 回退出稿 → 完整 _ask 旁注"证据降级"）
.venv/Scripts/python.exe -X utf8 ..\issues\evidence\_diag_a1_failfast_verify.py

# 端到端冒烟（真实 LLM，~10min）
.venv/Scripts/python.exe -X utf8 -m eval.run_eval --topics g01 --format lite --label smoke
```
