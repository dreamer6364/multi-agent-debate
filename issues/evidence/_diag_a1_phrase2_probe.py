"""A-1 第三轮探针：标题行内二分——卡片名 vs 书名/作者来源行。

已确认：Q0 全文 FAIL / Q1 前半 FAIL / Q2 后半 OK / Q3 标题行 FAIL。
本探针两枪：
  R1 仅卡片名「《卡片》数据提取的监控逻辑」
  R2 仅来源行「来源：《监控资本主义时代》（肖莎娜·祖博夫）｜书籍：」

用法（backend/ 目录）：
    .venv/Scripts/python.exe -X utf8 ../issues/evidence/_diag_a1_phrase2_probe.py
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

full = render_evidence([cards[1]])
header, _, body = full.partition("\n")
title_end = body.find("｜书籍：") + len("｜书籍：")
card_name = body[: body.find("｜来源：")]
source_line = body[body.find("｜来源：") + 1 : title_end]

variants = [
    ("R1 仅卡片名", f"{header}\n{card_name}"),
    ("R2 仅来源行（书名+作者）", f"{header}\n{source_line}"),
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
