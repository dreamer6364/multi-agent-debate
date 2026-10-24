"""辩题端点（SPEC §5.2）：内置 + 自建，归属隔离。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import or_, select

from ..db.models import DebateRow, TopicRow, iso_cn
from .deps import AppState, get_current_user, get_session, get_state
from .errors import ApiError

router = APIRouter(prefix="/api/topics", tags=["topics"])


class TopicIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    pro_points: str = Field(min_length=1)
    con_points: str = Field(min_length=1)
    pro_stance: str = ""
    con_stance: str = ""


def _derive_stance(title: str, side: str) -> str:
    """标题形如 'A / B'：立场缺省时取对应半句。"""
    parts = [p.strip() for p in title.split("/")]
    fallback = parts[0] if side == "pro" else (parts[1] if len(parts) > 1 else "")
    return f"支持：{fallback}" if fallback else ""


@router.get("")
async def list_topics(user=Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(TopicRow).where(or_(TopicRow.is_builtin.is_(True), TopicRow.owner_id == user.id))
        .order_by(TopicRow.is_builtin.desc(), TopicRow.id)
    )).scalars().all()
    return {"items": [
        {
            "id": r.id, "title": r.title,
            "pro_stance": r.pro_stance, "con_stance": r.con_stance,
            "pro_points": r.pro_points, "con_points": r.con_points,
            "is_builtin": r.is_builtin, "owner_id": r.owner_id,
            "created_at": iso_cn(r.created_at),
        } for r in rows
    ]}


@router.post("", status_code=201)
async def create_topic(body: TopicIn, user=Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    row = TopicRow(
        owner_id=user.id, title=body.title,
        pro_stance=body.pro_stance or _derive_stance(body.title, "pro"),
        con_stance=body.con_stance or _derive_stance(body.title, "con"),
        pro_points=body.pro_points, con_points=body.con_points,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "title": row.title}


@router.delete("/{topic_id}", status_code=204)
async def delete_topic(topic_id: int, request: Request, user=Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    state: AppState = get_state(request)
    row = await session.get(TopicRow, topic_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "辩题不存在")
    if row.is_builtin:
        raise ApiError(403, "FORBIDDEN", "内置辩题不可删除")
    if row.owner_id != user.id:
        raise ApiError(404, "NOT_FOUND", "辩题不存在")  # 不泄露他人资源存在性
    referenced = (await session.execute(
        select(DebateRow.id).where(DebateRow.topic_id == topic_id).limit(1)
    )).scalar_one_or_none()
    if referenced is not None:
        raise ApiError(409, "TOPIC_IN_USE", "辩题已被比赛引用，不可删除")
    await session.delete(row)
    await session.commit()
    return None
