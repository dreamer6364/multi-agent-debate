"""A-1 逐卡过滤探针：定位 g01 证据块中触发 GLM 1301 的具体卡片/结构。

前置结论（_diag_a1_filter_probe.py）：联网卡=0、无证据变体通过、全量/仅知识卡命中
1301 —— 触发源在 render_evidence(local_cards) 的 459 字内。本探针四枪：
  P0 card[0]《技术的本质》  P1 card[1]《监控资本主义》  P2 card[2]《技术垄断》  P3 仅表头行

用法（backend/ 目录）：
    .venv/Scripts/python.exe -X utf8 ../issues/evidence/_diag_a1_card_probe.py
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
from debate.engine.llm.gateway import Gateway, extra_body_from_env  # noqa: E402
from debate.engine.rag.retriever import EvidenceRetriever, render_evidence  # noqa: E402
from debate.engine.schema import Camp  # noqa: E402
from eval.dataset import load_golden_set  # noqa: E402

config = load_config(BACKEND / "config.yaml")
load_env(BACKEND / ".env")
gateway = Gateway(
    model_debater=os.environ.get("MODEL_DEBATER", ""),
    model_official=os.environ.get("MODEL_OFFICIAL", ""),
    max_retries=0, min_interval=0.0, extra_body=extra_body_from_env(),
)
item = next(i for i in load_golden_set() if i.id == "g01")
topic = item.to_topic()
retriever = EvidenceRetriever.from_dir(BACKEND / config.rag.knowledge_dir)
cards = retriever.evidence_for(topic, Camp.PRO, top_k=config.rag.top_k)

HEADER = "【证据参考】（可引用其观点与来源名，不得虚构卡外精确数据）"
INSTRUCTION = "开篇立论：下定义、立判准、铺论点（2~3 个），按论点→论据→论证展开。"

variants = [
    (f"P{i} card[{i}]", render_evidence([c])) for i, c in enumerate(cards)
] + [
    ("P3 仅表头", HEADER),
]

for name, evidence_text in variants:
    messages = build_debater_messages(
        spec=load_agents(BACKEND)["pro-1"],
        topic=topic, digest_text="", recent=[], evidence_text=evidence_text,
        instruction=INSTRUCTION, recent_k=config.context.recent_speeches,
        max_input_tokens=config.context.max_input_tokens,
    )
    t0 = time.monotonic()
    try:
        r = gateway.complete(messages, tier="debater",
                             temperature=config.model.temperature.debater, max_tokens=48)
        print(f"[{name}] OK {time.monotonic()-t0:.1f}s → {r.text[:50]!r}", flush=True)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        hit = "1301" in msg or "contentFilter" in msg
        print(f"[{name}] FAIL {time.monotonic()-t0:.1f}s "
              f"{'★1301' if hit else '其他'}: {msg[:200]}", flush=True)
