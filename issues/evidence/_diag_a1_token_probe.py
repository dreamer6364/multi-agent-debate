"""A-1 第四轮探针：来源行内单 token 定位（0.5s 秒拒 → 疑似关键词黑名单）。

已确认：R1 卡片名 OK / R2 来源行「来源：《监控资本主义时代》（肖莎娜·祖博夫）｜书籍：」FAIL。
五枪单 token：
  T1 监控   T2 资本主义   T3 监控资本主义   T4 肖莎娜·祖博夫   T5《监控资本主义时代》全书名

用法（backend/ 目录）：
    .venv/Scripts/python.exe -X utf8 ../issues/evidence/_diag_a1_token_probe.py
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
from debate.engine.rag.retriever import EvidenceRetriever  # noqa: E402
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

HEADER = "【证据参考】（可引用其观点与来源名，不得虚构卡外精确数据）"
tokens = ["监控", "资本主义", "监控资本主义", "肖莎娜·祖博夫", "《监控资本主义时代》"]

INSTRUCTION = "开篇立论：下定义、立判准、铺论点（2~3 个），按论点→论据→论证展开。"

for token in tokens:
    evidence_text = f"{HEADER}\n来源：{token}"
    messages = build_debater_messages(
        spec=spec, topic=topic, digest_text="", recent=[], evidence_text=evidence_text,
        instruction=INSTRUCTION, recent_k=config.context.recent_speeches,
        max_input_tokens=config.context.max_input_tokens,
    )
    t0 = time.monotonic()
    try:
        r = gateway.complete(messages, tier="debater",
                             temperature=config.model.temperature.debater, max_tokens=48)
        print(f"[T token={token!r}] OK {time.monotonic()-t0:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        hit = "1301" in msg or "contentFilter" in msg
        print(f"[T token={token!r}] FAIL {time.monotonic()-t0:.1f}s "
              f"{'★1301' if hit else '其他'}: {msg[:160]}", flush=True)
