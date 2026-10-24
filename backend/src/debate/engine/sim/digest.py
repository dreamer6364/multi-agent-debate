"""滚动战局摘要（SPEC §4.5）。

五栏结构：定义与判准 / 论点与论据 / 交锋点 / 谬误指控 / 局势小结。
合并规则：超长时先压缩"可压缩栏"（定义/论点/局势），交锋点与谬误指控
仅压缩到能容纳为止、条目永不删除——保障四辩复盘不漏战场。
原文摘录池随摘要抽取，供评委引用原话（AGENT_CONSTRAINTS §6）。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from pydantic import BaseModel, Field

from ..schema import CAMP_CN, Camp, Speech

COLUMNS = ("definition_judge", "arguments", "clashes", "fallacies", "situation")
CN: dict[str, str] = {
    "definition_judge": "定义与判准",
    "arguments": "论点与论据",
    "clashes": "交锋点",
    "fallacies": "谬误指控",
    "situation": "局势小结",
}
# 压缩顺序：先牺牲叙述性栏目，交锋点/谬误指控最后兜底
_COMPRESS_ORDER = ("definition_judge", "arguments", "situation", "clashes", "fallacies")


class Excerpt(BaseModel):
    phase: int
    speaker: str
    camp: Camp
    text: str


class Digest(BaseModel):
    definition_judge: str = ""
    arguments: str = ""
    clashes: str = ""
    fallacies: str = ""
    situation: str = ""
    excerpts: list[Excerpt] = Field(default_factory=list)


def extract_excerpts(
    speeches: list[Speech], per_phase: int = 4, min_chars: int = 40
) -> list[Excerpt]:
    """原文摘录池：每环节取前 per_phase 条长度达标的辩手发言（按 seq）。"""
    counts: dict[int, int] = defaultdict(int)
    out: list[Excerpt] = []
    for speech in sorted(speeches, key=lambda s: s.seq):
        if speech.camp is Camp.NEUTRAL:
            continue
        if len(speech.text) < min_chars:
            continue
        if counts[speech.phase] >= per_phase:
            continue
        counts[speech.phase] += 1
        out.append(Excerpt(phase=speech.phase, speaker=speech.speaker, camp=speech.camp, text=speech.text))
    return out


def merge_digest(
    prev: Digest,
    new_columns: dict[str, str],
    excerpts: list[Excerpt],
    max_chars: int,
    compress: Callable[[str, str], str],
) -> Digest:
    """合并上一版摘要与新环节五栏；compress(label_cn, text) 为压缩器（生产=LLM，测试=注入）。

   交锋点/谬误指控条目永不删除（SPEC §4.5），仅在极端超长时被压缩。
    """
    merged = Digest(excerpts=list(prev.excerpts) + list(excerpts))
    for key in COLUMNS:
        old = getattr(prev, key)
        new = (new_columns.get(key) or "").strip()
        if old and new:
            setattr(merged, key, f"{old}\n{new}")
        else:
            setattr(merged, key, old or new)

    def total_len() -> int:
        return sum(len(getattr(merged, key)) for key in COLUMNS)

    guard = 0
    while total_len() > max_chars and guard < 32:
        guard += 1
        compressed = False
        for key in _COMPRESS_ORDER:
            text = getattr(merged, key)
            if len(text) > 60:
                setattr(merged, key, compress(CN[key], text))
                compressed = True
                break
        if not compressed:
            break
    return merged


def render_digest(digest: Digest) -> str:
    """注入上下文用的摘要文本：五栏 + 原文摘录池（AGENT_CONSTRAINTS §6）。"""
    lines = [f"【{CN[key]}】{getattr(digest, key)}" for key in COLUMNS]
    excerpt_lines = [
        f"- [P{e.phase}][{CAMP_CN[e.camp]}·{e.speaker}] {e.text}" for e in digest.excerpts
    ]
    lines.append("【原文摘录】\n" + ("\n".join(excerpt_lines) if excerpt_lines else "（暂无）"))
    return "\n".join(lines)
