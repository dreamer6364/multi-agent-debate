"""ISS-015 #2/#3 真实验证探针（GLM glm-4.5-flash 实跑，归档 issues/evidence/）。

已知触发源（前七支探针定位）：证据卡来源行书名《监控资本主义时代》命中内容黑名单。
本探针验证修复后的两层行为：
  V1 网关 fail-fast：带触发词的 prompt → ContentFiltered 首打即抛（1 枪、无退避、计时留证）；
  V2 去证据回退：重建 evidence_text="" 的 messages → 成功出稿；
  V3 完整 _ask：真实 DebateContext 注入触发卡 → 正常发言 + 旁注"证据降级" + 降级计数 0。

用法（backend/ 目录）：
    .venv/Scripts/python.exe -X utf8 ../issues/evidence/_diag_a1_failfast_verify.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

from debate.core.config import load_config, load_env  # noqa: E402
from debate.engine.agents.context import build_debater_messages  # noqa: E402
from debate.engine.agents.loader import load_agents  # noqa: E402
from debate.engine.llm.gateway import ContentFiltered, Gateway, extra_body_from_env  # noqa: E402
from debate.engine.nodes.phases import _ask  # noqa: E402
from debate.engine.rag.corpus import EvidenceCard  # noqa: E402
from debate.engine.runtime import DebateContext  # noqa: E402
from debate.engine.schema import DebateFormat, Camp  # noqa: E402
from eval.dataset import load_golden_set  # noqa: E402

config = load_config(BACKEND / "config.yaml")
load_env(BACKEND / ".env")
gateway = Gateway(
    model_debater=os.environ.get("MODEL_DEBATER", ""),
    model_official=os.environ.get("MODEL_OFFICIAL", ""),
    max_retries=3, min_interval=0.0, extra_body=extra_body_from_env(),
)

# 调用计数：包装 _call_openai 统计真实 HTTP 打枪数（fail-fast 证据）
_orig_call = gateway._call_openai
_attempts: list[float] = []


def _counting(messages, model, temperature, max_tokens):
    _attempts.append(time.monotonic())
    return _orig_call(messages, model, temperature, max_tokens)


gateway._call_openai = _counting  # type: ignore[method-assign]

agents = load_agents(BACKEND)
spec = agents["pro-1"]
item = next(i for i in load_golden_set() if i.id == "g01")
topic = item.to_topic()

# 已知触发卡（修复前 evidence-base.md 的原始来源行，逐字复刻）
TRIGGER_TEXT = (
    "- 《卡片》数据提取的监控逻辑｜来源：《监控资本主义时代》（肖莎娜·祖博夫）｜书籍："
    "祖博夫提出“监控资本主义”概念：平台经济的行为剩余价值提取，将用户体验转为可预测、"
    "可出售的商品。当依赖成为默认，个体议价能力与退出选项被系统性侵蚀——"
    "这为“依赖需要警惕与制度制衡”提供了有力的批判视角。"
)
HEADER = "【证据参考】（可引用其观点与来源名，不得虚构卡外精确数据）"
INSTRUCTION = "开篇立论：下定义、立判准、铺论点（2~3 个），按论点→论据→论证展开。"

results: list[tuple[str, bool, str]] = []


def _report(step: str, ok: bool, detail: str) -> None:
    results.append((step, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {step}: {detail}", flush=True)


# ---- V1 网关 fail-fast ----
t0 = time.monotonic()
n_before = len(_attempts)
try:
    gateway.complete(
        build_debater_messages(
            spec=spec, topic=topic, digest_text="", recent=[],
            evidence_text=f"{HEADER}\n{TRIGGER_TEXT}", instruction=INSTRUCTION,
            recent_k=config.context.recent_speeches,
            max_input_tokens=config.context.max_input_tokens,
        ),
        tier="debater", temperature=config.model.temperature.debater, max_tokens=64,
    )
    _report("V1 网关 fail-fast", False, "预期 ContentFiltered，实际调用成功（黑名单可能已变）")
    filter_reproduced = False
except ContentFiltered as exc:
    elapsed = time.monotonic() - t0
    shots = len(_attempts) - n_before
    filter_reproduced = True
    ok = shots == 1
    _report("V1 网关 fail-fast", ok,
            f"ContentFiltered 首打即抛：{shots} 枪、{elapsed:.1f}s、无退避"
            f"（修复前 4 枪+退避）；msg={str(exc)[:80]}")
except Exception as exc:  # noqa: BLE001 —— 探针：原样留证
    _report("V1 网关 fail-fast", False, f"非预期异常 {type(exc).__name__}: {str(exc)[:160]}")
    filter_reproduced = False

# ---- V2 去证据回退（消息层） ----
t0 = time.monotonic()
try:
    r = gateway.complete(
        build_debater_messages(
            spec=spec, topic=topic, digest_text="", recent=[],
            evidence_text="", instruction=INSTRUCTION,
            recent_k=config.context.recent_speeches,
            max_input_tokens=config.context.max_input_tokens,
        ),
        tier="debater", temperature=config.model.temperature.debater, max_tokens=64,
    )
    _report("V2 去证据回退", True, f"{time.monotonic()-t0:.1f}s 出稿 → {r.text[:40]!r}")
except Exception as exc:  # noqa: BLE001
    _report("V2 去证据回退", False, f"{type(exc).__name__}: {str(exc)[:160]}")

# ---- V3 完整 _ask（真实 DebateContext + 触发卡注入） ----
trigger_card = EvidenceCard(
    card_id="trigger", title="数据提取的监控逻辑",
    source="《监控资本主义时代》（肖莎娜·祖博夫）", source_type="书籍",
    text="平台经济的行为剩余价值提取。", domain="科技",
)
ctx = DebateContext(
    config=config, topic=topic, agents=agents, gateway=gateway,
    compressor=lambda s: {}, format=DebateFormat.LITE,
    evidence={Camp.PRO: [trigger_card], Camp.CON: []},
    retriever=None, on_delta=None, on_event=None,
)
n_before = len(_attempts)
t0 = time.monotonic()
try:
    speech = _ask(
        ctx, speaker="pro-1", phase=1, phase_name="开篇立论",
        instruction=INSTRUCTION, cap_words=300, hard_cut=False,
        recent=[], digest_text="", phase_key=None,
    )
    shots = len(_attempts) - n_before
    if not filter_reproduced:
        _report("V3 完整 _ask", False, "触发源未复现，本步无意义（看 V1）")
    else:
        ok = ("证据降级" in speech.side_notes and ctx.degradation_streak == 0
              and not speech.text.startswith("[系统]"))
        _report(
            "V3 完整 _ask", ok,
            f"{time.monotonic()-t0:.1f}s、{shots} 枪、streak={ctx.degradation_streak}、"
            f"notes={speech.side_notes}、text={speech.text[:48]!r}",
        )
except Exception as exc:  # noqa: BLE001
    _report("V3 完整 _ask", False, f"{type(exc).__name__}: {str(exc)[:200]}")

print(f"[usage] calls={gateway.usage['calls']} prompt={gateway.usage['prompt_tokens']}"
      f" completion={gateway.usage['completion_tokens']}", flush=True)
failed = [s for s, ok, _ in results if not ok]
print(f"[verify] {'ALL PASS' if not failed else 'FAILED: ' + ', '.join(failed)}", flush=True)
raise SystemExit(0 if not failed else 1)
