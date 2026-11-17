"""A-1 内容过滤探针（归档 issues/evidence/）：定位 GLM 1301 contentFilter 触发源。

现象：eval 冒烟中辩手调用连续 400（contentFilter level=1, role=user），chair/judge 正常。
假设：辩手 prompt 独有的注入内容（联网搜索卡 / 本地知识卡 / 指令模板）触发误拦。

方法：构造与 run_debate 完全相同的 pro-1 立论 messages，逐层剥除注入源各打一枪：
  A 全量（知识卡 + 联网卡 + 完整指令）
  B 去联网卡（仅知识卡）
  C 去全部证据（仅辩题 + 角色 + 指令）
  D 仅辩题 + 角色（连环节指令也去，验证指令模板本身）

用法（backend/ 目录，与冒烟错开跑）：
    .venv/Scripts/python.exe -X utf8 ../issues/evidence/_diag_a1_filter_probe.py
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
from debate.engine.agents.loader import load_agents  # noqa: E402
from debate.engine.llm.gateway import Gateway, extra_body_from_env  # noqa: E402
from debate.engine.rag.retriever import EvidenceRetriever, render_evidence  # noqa: E402
from debate.engine.rag.websearch import resolve_provider, search_as_cards  # noqa: E402
from debate.engine.agents.context import build_debater_messages  # noqa: E402
from debate.engine.schema import Camp  # noqa: E402
from eval.dataset import load_golden_set  # noqa: E402

config = load_config(BACKEND / "config.yaml")
load_env(BACKEND / ".env")
gateway = Gateway(
    model_debater=os.environ.get("MODEL_DEBATER", ""),
    model_official=os.environ.get("MODEL_OFFICIAL", ""),
    max_retries=0,  # 不重试：我们要看原始错误
    min_interval=0.0,
    extra_body=extra_body_from_env(),
)
agents = load_agents(BACKEND)
spec = agents["pro-1"]
item = next(i for i in load_golden_set() if i.id == "g01")
topic = item.to_topic()

# ---- 注入源 ----
retriever = EvidenceRetriever.from_dir(BACKEND / config.rag.knowledge_dir)
local_cards = retriever.evidence_for(topic, Camp.PRO, top_k=config.rag.top_k)
web_cards = []
if config.web_search.enabled:
    provider = resolve_provider() if config.web_search.provider == "env" else config.web_search.provider
    web_cards = search_as_cards(f"{topic.title} {topic.stance_for(Camp.PRO)}",
                                top_k=config.web_search.top_k, provider=provider)
print(f"[probe] 知识卡 {len(local_cards)} 张 | 联网卡 {len(web_cards)} 张", flush=True)
for i, card in enumerate(web_cards):
    title = getattr(card, "title", None) or (card.get("title") if isinstance(card, dict) else "?")
    print(f"   web[{i}] {title}", flush=True)

INSTRUCTION = "开篇立论：下定义、立判准、铺论点（2~3 个），按论点→论据→论证展开。"


def build(evidence_text: str, instruction: str) -> list[dict[str, str]]:
    return build_debater_messages(
        spec=spec, topic=topic, digest_text="", recent=[],
        evidence_text=evidence_text, instruction=instruction,
        recent_k=config.context.recent_speeches,
        max_input_tokens=config.context.max_input_tokens,
    )


VARIANTS = [
    ("A 全量（知识+联网+完整指令）", build(render_evidence(list(local_cards) + list(web_cards)), INSTRUCTION)),
    ("B 仅知识卡", build(render_evidence(local_cards), INSTRUCTION)),
    ("C 无证据（仅辩题+角色+指令）", build("", INSTRUCTION)),
    ("D 仅辩题+角色（无指令）", build("", "请发言。")),
]

for name, messages in VARIANTS:
    prompt_chars = sum(len(m.get("content", "")) for m in messages)
    t0 = time.monotonic()
    try:
        r = gateway.complete(messages, tier="debater",
                             temperature=config.model.temperature.debater, max_tokens=64)
        print(f"[{name}] OK {time.monotonic()-t0:.1f}s chars={prompt_chars} "
              f"→ {r.text[:60]!r}", flush=True)
    except Exception as exc:  # noqa: BLE001 —— 探针：打印原始错误
        msg = str(exc)
        hit = "1301" in msg or "contentFilter" in msg
        print(f"[{name}] FAIL {time.monotonic()-t0:.1f}s chars={prompt_chars} "
              f"{'★命中内容过滤' if hit else ''}: {msg[:260]}", flush=True)
