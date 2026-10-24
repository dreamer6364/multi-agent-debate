"""计时模拟（SPEC §4.2，ARCH D2）。

时长是模拟口径：字数预算 = 时长(分钟) × 语速(字/分钟)，与墙钟无关。
计时员是确定性代码，不是 LLM；旁注文案锁死（CONSTRAINTS F8 同源逻辑）。
"""
from __future__ import annotations

import re

from ..schema import CAMP_CN, Camp

NOTE_WARNING = "计时员：还剩 30 秒"
NOTE_TIME_UP = "计时员：时间到"


def words_for(minutes: float, speed_cpm: int) -> int:
    """时长（分钟）→ 字数预算。"""
    return round(minutes * speed_cpm)


_SENT_END = re.compile(r"[。！？!?；;]")


def truncate_to_sentence(text: str, max_chars: int) -> tuple[str, bool]:
    """截断到 max_chars 内最后一个完整句边界（。！？!?；;）；预算内无句读则硬截。

    返回 (文本, 是否截断)。
    """
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    ends = [m.end() for m in _SENT_END.finditer(cut)]
    return (cut[: ends[-1]], True) if ends else (cut, True)


def truncate_answer_on_interrupt(text: str) -> tuple[str, bool]:
    """被打断回答的截断口径：保留约前一半字数的完整句（sim 模拟，见 interrupt 模块说明）。"""
    return truncate_to_sentence(text, max(1, len(text) // 2))


class TurnClock:
    """单环节发言预算：警告一次、叫停一次，均不重复（SPEC §4.2）。"""

    def __init__(self, budget_words: int, speed_cpm: int = 220):
        self.remaining = budget_words
        self.warn_threshold = round(speed_cpm * 0.5)
        self._warned = False
        self._announced = False

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def consume(self, words: int) -> list[str]:
        """消耗字数并返回本步触发的旁注。"""
        self.remaining -= words
        notes: list[str] = []
        if not self._warned and self.remaining <= self.warn_threshold:
            self._warned = True
            notes.append(NOTE_WARNING)
        if self.remaining <= 0 and not self._announced:
            self._announced = True
            notes.append(NOTE_TIME_UP)
        return notes


class CampPool:
    """自由辩论单方时间池：警告一次/池，耗尽宣布"X 方时间用尽"。"""

    def __init__(self, camp: Camp, budget_words: int, speed_cpm: int = 220):
        self.camp = camp
        self.remaining = budget_words
        self.warn_threshold = round(speed_cpm * 0.5)
        self._warned = False

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def consume(self, words: int) -> list[str]:
        self.remaining -= words
        notes: list[str] = []
        if not self._warned and self.remaining <= self.warn_threshold:
            self._warned = True
            notes.append(NOTE_WARNING)
        if self.remaining <= 0:
            notes.append(f"{CAMP_CN[self.camp]}时间用尽")
        return notes


class FreeDebateClock:
    """自由辩论双池分别计时（PRD §4.2：一方用尽，另一方连讲至用完）。"""

    def __init__(self, minutes_per_camp: float, speed_cpm: int, turn_cap_minutes: float):
        budget = words_for(minutes_per_camp, speed_cpm)
        self.pools: dict[Camp, CampPool] = {
            Camp.PRO: CampPool(Camp.PRO, budget, speed_cpm),
            Camp.CON: CampPool(Camp.CON, budget, speed_cpm),
        }
        self.turn_cap_words = words_for(turn_cap_minutes, speed_cpm)

    def consume(self, camp: Camp, words: int) -> list[str]:
        return self.pools[camp].consume(words)

    @property
    def finished(self) -> bool:
        return all(pool.exhausted for pool in self.pools.values())

    @staticmethod
    def other(camp: Camp) -> Camp:
        return Camp.CON if camp is Camp.PRO else Camp.PRO
