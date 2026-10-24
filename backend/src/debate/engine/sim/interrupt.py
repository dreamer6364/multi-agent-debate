"""打断规则（SPEC §4.3 / 守则六）。

模拟口径（对 SPEC §4.3 的落地解释，待下次 SPEC 修订合入）：
LLM 调用是离散步骤，无法在对方生成中途插入。因此 [打断] 标记只允许出现在
**质询方（phase 3 提问方）的发言中**，作用于其上一条被质询回答——
该回答被标记 interrupted、原文截断至约前一半的完整句（见 apply_interrupt_to_answer）。
标记超限（每回答 >2 次）时标记失效并旁注违规。
"""
from __future__ import annotations

import re

from ..schema import Speech
from .timer import truncate_answer_on_interrupt

_MARKER = re.compile(r"[【\[]\s*打断\s*[】\]]\s*")
_REASON_STOP = "。！？!?；;\n"


def parse_interrupt(text: str) -> tuple[str, str | None]:
    """从质询方发言解析 [打断]<理由> 标记。

    返回 (剥离标记后的正文, 理由)。无标记时理由为 None；
    标记后无文字时理由为空串（视为未说明理由，由调用方记违规）。
    """
    match = _MARKER.search(text)
    if match is None:
        return text, None
    start = match.end()
    end = len(text)
    for offset, ch in enumerate(text[start:], start=start):
        if ch in _REASON_STOP:
            end = offset + 1  # 句读归入理由，正文从下一句开始
            break
    reason = text[start:end].strip()
    clean = (text[: match.start()] + text[end:]).strip()
    return clean, reason


class InterruptLedger:
    """按被质询回答计数的打断额度（守则六：每回答至多 2 次）。"""

    def __init__(self, max_per_answer: int = 2):
        self.max_per_answer = max_per_answer
        self._counts: dict[str, int] = {}

    def remaining(self, answer_key: str) -> int:
        return self.max_per_answer - self._counts.get(answer_key, 0)

    def try_consume(self, answer_key: str) -> bool:
        """消耗一次额度；False = 已超限（标记失效）。"""
        used = self._counts.get(answer_key, 0)
        if used >= self.max_per_answer:
            return False
        self._counts[answer_key] = used + 1
        return True


def apply_interrupt_to_answer(answer: Speech, reason: str) -> Speech:
    """把一次合法打断落到被质询回答上：标记 interrupted、截断原文、追加旁注。"""
    cut, _ = truncate_answer_on_interrupt(answer.text)
    answer.text = cut
    answer.word_count = len(cut)
    answer.interrupted = True
    answer.side_notes.append(f"质询方打断：{reason}")
    return answer
