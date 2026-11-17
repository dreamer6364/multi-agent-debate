"""A-1 卡内二分探针：定位 card[1]《监控资本主义时代》中触发 1301 的具体片段。

已确认：P0《技术的本质》OK、P1 本卡 FAIL、P2《技术垄断》OK、P3 仅表头 OK。
本探针对 card[1] 的**真实渲染文本**切片（不手工重打，保证对照枪与 P1 逐字一致）：
  Q0 全文(对照) / Q1 前半(标题+概念句) / Q2 后半(侵蚀+制衡) / Q3 标题行

用法（backend/ 目录）：
    .venv/Scripts/python.exe -X utf8 ../issues/evidence/_diag_a1_phrase_probe.py
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
spec = load_agents(BACKEND)["pro-1"]

full = render_evidence([cards[1]])  # 与探针 P1 逐字一致（FAIL 已复现）
header, _, body = full.partition("\n")
split = body.find("当依赖成为默认")
title_end = body.find("｜书籍：") + len("｜书籍：")
assert split > 0 and title_end > len("｜书籍："), "切分点未找到"

variants = [
    ("Q0 全文（对照）", full),
    ("Q1 前半（标题+概念+提取+商品）", f"{header}\n{body[:split]}"),
    ("Q2 后半（侵蚀+制衡）", f"{header}\n{body[split:]}"),
    ("Q3 标题行（卡片名+书名+作者）", f"{header}\n{body[:title_end]}"),
]

INSTRUCTION = "开篇立论：下定义、立判准、铺论点（2~3 个），按论点→论据→论证展开。"

for name, evidence_text in variants:
    messages = build_debater_messages(
        spec=spec, topic=topic, digest_text="", recent=[], evidence_text=evidence_text,
        instruction=INSTRUCTION, recent_k=config.context.recent_speeches,
        max_input_tokens=config.context.max_input_tokens,
    )
    t0 = time.monotonic()
    try:
        r = gateway.complete(messages, tier="debater",
                             temperature=config.model.temperature.debater, max_tokens=48)
        print(f"[{name}] OK {time.monotonic()-t0:.1f}s → {r.text[:46]!r}", flush=True)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        hit = "1301" in msg or "contentFilter" in msg
        print(f"[{name}] FAIL {time.monotonic()-t0:.1f}s "
              f"{'★1301' if hit else '其他'}: {msg[:200]}", flush=True)
