"""五张表模型（SPEC §3，deviation：topics 增加双方立场列、debates 增加 votes_json，随下次 SPEC 修订合入）。

时间统一存东八区 naive 值（CONSTRAINTS §3.2）；JSON 列用 SQLAlchemy 通用 JSON（MySQL/SQLite 皆可）。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Column, Index, JSON, Text, UniqueConstraint
from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel

_TZ = ZoneInfo("Asia/Shanghai")


def now_cn() -> datetime:
    """当前东八区时间（naive 存储）。"""
    return datetime.now(_TZ).replace(tzinfo=None)


def iso_cn(value: datetime | None) -> str | None:
    """DB naive 值 → ISO 8601 +08:00 输出（CONSTRAINTS §3.2）。"""
    if value is None:
        return None
    return value.replace(tzinfo=_TZ).isoformat()


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(max_length=32, sa_column_kwargs={"unique": True})
    password_hash: str = Field(max_length=72)
    role: str = Field(default="user", max_length=16)
    created_at: NaiveDatetime = Field(default_factory=now_cn)


class TopicRow(SQLModel, table=True):
    __tablename__ = "topics"

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int | None = Field(default=None, foreign_key="users.id")
    title: str = Field(max_length=200)
    pro_stance: str = Field(default="")
    con_stance: str = Field(default="")
    pro_points: str = Field(sa_column=Column(Text))
    con_points: str = Field(sa_column=Column(Text))
    is_builtin: bool = Field(default=False)
    created_at: NaiveDatetime = Field(default_factory=now_cn)

    __table_args__ = (Index("idx_topics_owner", "owner_id"),)


class DebateRow(SQLModel, table=True):
    __tablename__ = "debates"

    id: int | None = Field(default=None, primary_key=True)
    topic_id: int = Field(foreign_key="topics.id")
    owner_id: int = Field(foreign_key="users.id")
    format: str = Field(max_length=8)  # full | lite
    status: str = Field(default="pending", max_length=16)  # pending/running/finished/failed/stopped
    cfg_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    winner: str | None = Field(default=None, max_length=8)
    best_speaker: str | None = Field(default=None, max_length=16)
    team_total_pro: float | None = None
    team_total_con: float | None = None
    votes_json: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    usage_json: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    started_at: NaiveDatetime | None = None
    finished_at: NaiveDatetime | None = None
    created_at: NaiveDatetime = Field(default_factory=now_cn)

    __table_args__ = (
        Index("idx_debates_owner_status", "owner_id", "status"),
        Index("idx_debates_topic", "topic_id"),
    )


class SpeechRow(SQLModel, table=True):
    __tablename__ = "speeches"

    id: int | None = Field(default=None, primary_key=True)
    debate_id: int = Field(foreign_key="debates.id")
    seq: int
    phase: int
    phase_name: str = Field(max_length=32)
    speaker_role: str = Field(max_length=16)
    camp: str = Field(max_length=8)
    text: str = Field(sa_column=Column(Text))
    side_notes: list = Field(default_factory=list, sa_column=Column(JSON))
    interrupted: bool = False
    word_count: int = 0
    tokens: int = 0
    created_at: NaiveDatetime = Field(default_factory=now_cn)

    __table_args__ = (
        UniqueConstraint("debate_id", "seq", name="uq_debate_seq"),
        Index("idx_speeches_debate", "debate_id"),
    )


class ScorecardRow(SQLModel, table=True):
    __tablename__ = "scorecards"

    id: int | None = Field(default=None, primary_key=True)
    debate_id: int = Field(foreign_key="debates.id")
    judge: str = Field(max_length=32)
    focus: str = Field(max_length=16)
    scores_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    vote: str | None = Field(default=None, max_length=8)
    best_speaker: str | None = Field(default=None, max_length=16)
    highlights_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    weaknesses_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    abstained: bool = False
    created_at: NaiveDatetime = Field(default_factory=now_cn)

    __table_args__ = (UniqueConstraint("debate_id", "judge", name="uq_debate_judge"),)
