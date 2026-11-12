"""辩题端点（SPEC §5.2）：内置 + 自建，归属隔离。

缓存口径（v2.10）：GET 列表走只读旁路缓存（cache-aside），键按用户隔离；
本文件的写路径（新建/删除）与 debates.py 的自建辩题路径提交后主动失效对应键，
启动播种内置辩题后全量清前缀，TTL 60s 兜底漏失效——DB 始终是唯一事实来源。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import or_, select

from ..db.models import DebateRow, ScorecardRow, SpeechRow, TopicRow, iso_cn
from ..services.cache import cache_key, dumps, loads
from .deps import AppState, get_current_user, get_session, get_state
from .errors import ApiError

router = APIRouter(prefix="/api/topics", tags=["topics"])

TOPIC_LIST_TTL = 60  # 秒：漏失效的最大陈旧窗口（主动失效为主，TTL 只兜底）


def _list_cache_key(user_id: int) -> str:
    # 键含用户 id：列表 = 内置 + 自己的，天然按用户隔离（与 IDOR 边界一致）
    return cache_key("topics", "list", user_id)


class TopicIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    pro_points: str = Field(min_length=1)
    con_points: str = Field(min_length=1)
    pro_stance: str = ""
    con_stance: str = ""


def _derive_stance(title: str, side: str) -> str:
    """标题形如 'A / B'：立场缺省时取对应半句（v2.3：不再加「支持：」前缀，也不再整句充当立场）。

    标题不含「/」时返回空串——此时正反方立场应由前端回落到用户填写的破题要点首行，
    绝不能把整句辩题塞给正方、反方留空（曾导致横幅显示"支持：整句辩题 / 反方"）。
    """
    parts = [p.strip() for p in title.split("/") if p.strip()]
    if len(parts) < 2:
        return ""
    return parts[0] if side == "pro" else parts[1]


@router.get("")
async def list_topics(user=Depends(get_current_user), session: AsyncSession = Depends(get_session),
                      state: AppState = Depends(get_state)):
    # cache-aside：命中直接返回（含反序列化）；miss 回源 DB 并回填
    if state.cache is not None:
        cached = loads(await state.cache.get(_list_cache_key(user.id)))
        if cached is not None:
            return cached
    rows = (await session.execute(
        select(TopicRow).where(or_(TopicRow.is_builtin.is_(True), TopicRow.owner_id == user.id))
        .order_by(TopicRow.is_builtin.desc(), TopicRow.id)
    )).scalars().all()
    payload = {"items": [
        {
            "id": r.id, "title": r.title,
            "pro_stance": r.pro_stance, "con_stance": r.con_stance,
            "pro_points": r.pro_points, "con_points": r.con_points,
            "is_builtin": r.is_builtin, "owner_id": r.owner_id,
            "created_at": iso_cn(r.created_at),
        } for r in rows
    ]}
    if state.cache is not None:
        await state.cache.set(_list_cache_key(user.id), dumps(payload), TOPIC_LIST_TTL)
    return payload


async def invalidate_topic_list(state: AppState, owner_id: int) -> None:
    """写路径调用：新建/删除辩题后使该用户的列表缓存立即失效（不等 TTL）。"""
    if state.cache is not None:
        await state.cache.delete(_list_cache_key(owner_id))


@router.post("", status_code=201)
async def create_topic(body: TopicIn, user=Depends(get_current_user),
                       session: AsyncSession = Depends(get_session),
                       state: AppState = Depends(get_state)):
    row = TopicRow(
        owner_id=user.id, title=body.title,
        pro_stance=body.pro_stance or _derive_stance(body.title, "pro"),
        con_stance=body.con_stance or _derive_stance(body.title, "con"),
        pro_points=body.pro_points, con_points=body.con_points,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await invalidate_topic_list(state, user.id)  # 缓存失效：新辩题须立即可见
    return {"id": row.id, "title": row.title}


@router.delete("/{topic_id}", status_code=204)
async def delete_topic(topic_id: int, request: Request, force: bool = False,
                       user=Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    """删除辩题。

    force=false（默认）：被比赛引用时拒绝，历史归档不受影响；
    force=true：连同该辩题名下的历史场次（发言、评分卡）一并删除——
    数据库里 debates.topic_id 是外键，不先清场次就删不掉辩题；仍在进行中的场次一律拒绝。
    """
    state: AppState = get_state(request)
    row = await session.get(TopicRow, topic_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "辩题不存在")
    if row.is_builtin:
        raise ApiError(403, "FORBIDDEN", "内置辩题不可删除")
    if row.owner_id != user.id:
        raise ApiError(404, "NOT_FOUND", "辩题不存在")  # 不泄露他人资源存在性

    referenced = (await session.execute(
        select(DebateRow).where(DebateRow.topic_id == topic_id)
    )).scalars().all()
    if referenced:
        if not force:
            raise ApiError(
                409, "TOPIC_IN_USE",
                f"该辩题已被 {len(referenced)} 场比赛引用；如需删除，请选择「连同历史场次一起删除」",
            )
        if any(r.status in ("pending", "running") for r in referenced):
            raise ApiError(409, "TOPIC_IN_USE", "该辩题还有进行中的比赛，请先终止比赛再删除")
        ids = [int(r.id) for r in referenced if r.id is not None]
        await session.execute(delete(ScorecardRow).where(ScorecardRow.debate_id.in_(ids)))
        await session.execute(delete(SpeechRow).where(SpeechRow.debate_id.in_(ids)))
        await session.execute(delete(DebateRow).where(DebateRow.id.in_(ids)))

    await session.delete(row)
    await session.commit()
    await invalidate_topic_list(state, user.id)  # 缓存失效：删除须立即反映
    return None
