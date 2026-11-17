# ISS-015（生产）内容过滤误拦证据卡书名 → 连续降级熔断整场失败，无注入降级回退

**状态**：部分修复（资产层已修，2/3 待修复）
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
| 1 | **资产层** | `knowledge/evidence-base.md` 来源行改写：`《监控资本主义时代》（肖莎娜·祖博夫）` → `肖莎娜·祖博夫（平台经济批判著作）`（黑名单匹配书名文本，作者直引规避；正文"监控资本主义"概念裸词已验证安全） | ✅ 已修（改写候选经 V1/V2 探针验证：裸书名仍拦、仅作者通过） |
| 2 | **网关层** | `Gateway` 对**不可重试错误**（400 contentFilter/参数错）fail-fast：首次即抛、不进 3 次重试 + 90s 退避（当前每次白烧 ~150s）。归类：`RateLimit/429` 长退避、`contentFilter/400` 立即失败、其余指数退避 | 待修复（~0.5h，含测试） |
| 3 | **引擎层** | 辩手调用命中内容过滤且 prompt 含注入证据时 → **自动去证据重试一次**（复用 `build_debater_messages` 重建，evidence_text=""）→ 成功则继续并在 side_notes 标注"证据降级"，仍失败才走占位降级。防**未来任何注入源**（新知识卡/联网结果）误拦导致整场熔断 | 待修复（1~1.5h，含测试与 SPEC §4.9 同步） |

**残余风险**：黑名单是供应商黑盒且会演进——其他知识卡书名、其他辩题未逐一探过；
因此 #3（引擎回退）才是根治，#1 只是解除当前已知卡点。

## 4. 预估工时

资产 0.1h（已修）+ 网关 fail-fast 0.5h + 引擎证据降级回退 1~1.5h ≈ **2~2.1h**。

## 5. 复现/核验命令

```powershell
# 资产层修复后复现（应全过）
cd backend
.venv/Scripts/python.exe -X utf8 ..\issues\evidence\_diag_a1_card_probe.py   # P1 应 OK

# 端到端冒烟（真实 LLM，~10min）
.venv/Scripts/python.exe -X utf8 -m eval.run_eval --topics g01 --format lite --label smoke
```
