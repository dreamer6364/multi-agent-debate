"""赛制流程计划（SPEC §4.1 环节表）：环节顺序、编号、名称、时长来源。"""
from __future__ import annotations

from ..core.config import AppConfig
from .schema import DebateFormat

PHASE_NAMES: dict[str, str] = {
    "opening": "主席开场",
    "statement": "开篇立论",
    "cross_examination": "质询一辩",
    "rebuttal": "驳论",
    "attack": "攻辩盘问",
    "attack_summary": "攻辩小结",
    "free_debate": "自由辩论",
    "closing": "总结陈词",
    "judging": "评委点评",
    "verdict": "宣判闭幕",
}

PHASE_NUMBERS: dict[str, int] = {
    "opening": 1,
    "statement": 2,
    "cross_examination": 3,
    "rebuttal": 4,
    "attack": 5,
    "attack_summary": 6,
    "free_debate": 7,
    "closing": 8,
    "judging": 9,
    "verdict": 10,
}

FULL_SEQUENCE = [
    "opening", "statement", "cross_examination", "rebuttal", "attack",
    "attack_summary", "free_debate", "closing", "judging", "verdict",
]
LITE_SEQUENCE = ["statement", "rebuttal", "free_debate", "closing", "judging", "verdict"]


def sequence_for(fmt: DebateFormat) -> list[str]:
    return list(FULL_SEQUENCE if fmt is DebateFormat.FULL else LITE_SEQUENCE)


def minutes_for(phase_key: str, config: AppConfig) -> float:
    return float(getattr(config.procedure.phases, phase_key))
