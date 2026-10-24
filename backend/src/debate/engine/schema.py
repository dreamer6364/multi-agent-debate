"""引擎数据结构（SPEC §6）。字段命名 snake_case，与 DB/API 一致（CONSTRAINTS §3.1）。"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Camp(str, Enum):
    PRO = "pro"
    CON = "con"
    NEUTRAL = "neutral"


class DebateFormat(str, Enum):
    FULL = "full"
    LITE = "lite"


class DebateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    STOPPED = "stopped"


CAMP_CN: dict[Camp, str] = {
    Camp.PRO: "正方",
    Camp.CON: "反方",
    Camp.NEUTRAL: "中立",
}


class Topic(BaseModel):
    """辩题（来源 topics/*.md，M3 起入库 topics 表）。"""

    topic_id: str
    title: str
    pro_stance: str
    con_stance: str
    pro_points: list[str]
    con_points: list[str]

    def points_for(self, camp: Camp) -> list[str]:
        if camp is Camp.PRO:
            return self.pro_points
        if camp is Camp.CON:
            return self.con_points
        return []

    def stance_for(self, camp: Camp) -> str:
        if camp is Camp.PRO:
            return self.pro_stance
        if camp is Camp.CON:
            return self.con_stance
        return ""


class Speech(BaseModel):
    """实录最小单元，对应 DB speeches 行。"""

    seq: int
    phase: int
    phase_name: str
    speaker: str  # role_id
    camp: Camp
    text: str
    side_notes: list[str] = Field(default_factory=list)
    interrupted: bool = False
    word_count: int = 0
    tokens: int = 0


class ScoreCard(BaseModel):
    """评委评分卡：评委 JSON 输出经校验后的结构（SPEC §4.7）。"""

    judge: str
    focus: str
    pro_scores: dict[str, int]
    con_scores: dict[str, int]
    vote: Camp | None = None
    best_speaker: str | None = None
    highlights: dict[Camp, list[str]] = Field(default_factory=dict)
    weaknesses: dict[Camp, list[str]] = Field(default_factory=dict)
    abstained: bool = False


class Verdict(BaseModel):
    """胜负判定结果（SPEC §4.8 三级决胜）。"""

    votes: dict[Camp, int]
    team_totals: dict[Camp, float]
    winner: Camp | None
    best_speaker: str | None = None
    decisive_rule: str  # majority | team_total | best_speaker | unresolved
