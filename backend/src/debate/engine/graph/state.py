"""图全局状态（ARCH §4.2）。依赖经闭包注入，状态只承载可演进的数据。"""
from __future__ import annotations

from typing import TypedDict

from ..schema import ScoreCard, Speech, Verdict
from ..sim.digest import Digest


class DebateState(TypedDict, total=False):
    transcript: list[Speech]
    digest: Digest
    scorecards: list[ScoreCard]
    verdict: Verdict | None


def initial_state() -> DebateState:
    return {"transcript": [], "digest": Digest(), "scorecards": [], "verdict": None}
