"""战局摘要压缩器：把一个环节的新增发言压成五栏（SPEC §4.5）。

生产实现走 MODEL_OFFICIAL（digest 档）；解析失败降级为"尾部截断"，
摘要链路永不中断比赛（对齐 SPEC §4.9 精神）。
"""
from __future__ import annotations

import json
import re
from typing import Callable

from ..schema import Speech
from .gateway import Gateway

_COLUMNS = ("definition_judge", "arguments", "clashes", "fallacies", "situation")
_FENCE = re.compile(r"```[a-zA-Z]*")

_PROMPT = (
    "你是辩论赛的战局摘要器。阅读以下发言，输出一个 JSON 对象，字段为五栏："
    'definition_judge（定义与判准现状）、arguments（论点与论据）、clashes（交锋点，逐条列出）、'
    "fallacies（双方指出的对方谬误）、situation（局势小结）。每栏一到两句话，中文。只输出 JSON。"
)


def _extract_columns(raw: str) -> dict[str, str] | None:
    text = _FENCE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {key: str(data.get(key, "")).strip() for key in _COLUMNS}


def make_llm_columns_compressor(gateway: Gateway, *, temperature: float) -> Callable[[list[Speech]], dict[str, str]]:
    def compress(speeches: list[Speech]) -> dict[str, str]:
        if not speeches:
            return {}
        body = "\n".join(f"[{speech.speaker}] {speech.text}" for speech in speeches)
        for _ in range(2):  # 解析失败重试 1 次，再失败走降级
            try:
                result = gateway.complete(
                    [{"role": "user", "content": f"{_PROMPT}\n\n{body}"}],
                    tier="digest",
                    temperature=temperature,
                    max_tokens=500,
                )
            except Exception:  # noqa: BLE001 —— 摘要降级不中断比赛
                break
            columns = _extract_columns(result.text)
            if columns is not None:
                return columns
        tail = body[-200:]
        return {"situation": f"（摘要降级）最近发言尾部：{tail}"}

    return compress
