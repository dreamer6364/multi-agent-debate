"""比赛端点（SPEC §5.2/§6）：发起（配额闸）、列表、详情、发言、终止、导出、SSE 直播。归属隔离。"""
from __future__ import annotations

import asyncio
import json
from collections import Counter

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from ..db.models import DebateRow, ScorecardRow, SpeechRow, TopicRow, iso_cn
from ..engine.render.markdown import render_transcript_md
from ..engine.render.html import render_report_html
from ..engine.render.xlsx import render_scorecard_xlsx
from ..engine.runner import DebateResult
from ..engine.schema import Camp, DebateFormat, ScoreCard, Speech, Topic, Verdict
from ..services.debate_service import QuotaExceeded
from .deps import AppState, get_current_user, get_session, get_state
from .errors import ApiError

router = APIRouter(prefix="/api/debates", tags=["debates"])


class CustomTopicIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    pro_points: str = Field(min_length=1)
    con_points: str = Field(min_length=1)
    pro_stance: str = ""
    con_stance: str = ""


class DebateCreateIn(BaseModel):
    topic_id: int | None = None
    custom_topic: CustomTopicIn | None = None
    format: str = "full"
    judges: int = 3


def _fmt_num(value: float | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


async def _owned_debate(session: AsyncSession, debate_id: int, user_id: int) -> DebateRow:
    debate = await session.get(DebateRow, debate_id)
    if debate is None or debate.owner_id != user_id:
        raise ApiError(404, "NOT_FOUND", "比赛不存在")  # 不泄露他人资源存在性
    return debate


async def _load_topic_row(session: AsyncSession, topic_id: int) -> TopicRow:
    row = await session.get(TopicRow, topic_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "辩题不存在")
    return row


def _row_to_engine_topic(row: TopicRow) -> Topic:
    return Topic(
        topic_id=str(row.id), title=row.title,
        pro_stance=row.pro_stance, con_stance=row.con_stance,
        pro_points=[p for p in row.pro_points.split("\n") if p.strip()],
        con_points=[p for p in row.con_points.split("\n") if p.strip()],
    )


@router.post("", status_code=202)
async def create_debate(body: DebateCreateIn, request: Request,
                        user=Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    state: AppState = get_state(request)
    if (body.topic_id is None) == (body.custom_topic is None):
        raise ApiError(422, "VALIDATION_ERROR", "topic_id 与 custom_topic 二选一")
    if body.format not in ("full", "lite"):
        raise ApiError(422, "VALIDATION_ERROR", "format 仅允许 full / lite")
    if body.judges not in (1, 3, 5):
        raise ApiError(422, "VALIDATION_ERROR", "judges 仅允许 1 / 3 / 5")

    # ISS-001/002：配额检查与落库同一临界区——先查配额，辩题与场次同事务提交；
    # 任何一步失败整体回滚，既堵 TOCTOU 竞态，也不再留下"429 孤儿辩题"。
    async with state.service.quota_guard(user.id):
        if body.custom_topic is None:
            topic_row = await _load_topic_row(session, body.topic_id)
            if not topic_row.is_builtin and topic_row.owner_id != user.id:
                raise ApiError(404, "NOT_FOUND", "辩题不存在")
        try:
            await state.service.check_quota(user.id)
        except QuotaExceeded as exc:
            raise ApiError(429, exc.code, exc.message) from exc

        if body.custom_topic is not None:
            ct = body.custom_topic
            topic_row = TopicRow(
                owner_id=user.id, title=ct.title,
                pro_stance=ct.pro_stance or f"支持：{ct.title.split('/')[0].strip()}",
                con_stance=ct.con_stance or f"支持：{ct.title.split('/')[-1].strip()}",
                pro_points=ct.pro_points, con_points=ct.con_points,
            )
            session.add(topic_row)
            await session.flush()  # 取得 id；与场次同事务，不单独 commit

        debate = DebateRow(
            topic_id=topic_row.id, owner_id=user.id, format=body.format, status="pending",
            cfg_json={
                "judges": body.judges,
                "speed_cpm": state.config.procedure.speed_cpm,
                "phase_durations": state.config.procedure.phases.model_dump(),
            },
        )
        session.add(debate)
        await session.commit()
        if body.custom_topic is not None:
            # 自建辩题随场次同事务落库（ISS-002）：提交后须使列表缓存失效，新辩题立即可见
            from .topics import invalidate_topic_list
            await invalidate_topic_list(state, user.id)

    # v2.9.4 修复（BE-P2-1）：start 失败（如 .env 缺模型配置、网关初始化异常）不能留
    # 孤儿 pending——pending 计入并发配额且上限为 1，幽灵场次会 429 堵死该用户全部后续创建。
    try:
        state.service.start(debate_id=debate.id, topic=_row_to_engine_topic(topic_row),
                            fmt=DebateFormat(body.format))
    except Exception as exc:
        async with state.sessionmaker() as fix_session:  # 原会话已关闭，重开一个回写终态
            fix = await fix_session.get(DebateRow, debate.id)
            if fix is not None and fix.status == "pending":
                fix.status = "failed"
                fix.error = f"启动失败：{exc}"
                await fix_session.commit()
        raise ApiError(500, "debate_start_failed", f"比赛启动失败：{exc}") from exc
    return {"id": debate.id, "status": debate.status}


@router.get("")
async def list_debates(request: Request, status: str | None = None, page: int = 1, page_size: int = 20,
                       user=Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    conditions = [DebateRow.owner_id == user.id]
    if status:
        conditions.append(DebateRow.status == status)
    total = (await session.execute(
        select(func.count()).select_from(DebateRow).where(*conditions)
    )).scalar_one()
    rows = (await session.execute(
        select(DebateRow).where(*conditions).order_by(DebateRow.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    items = []
    for row in rows:
        topic_row = await _load_topic_row(session, row.topic_id)
        items.append({
            "id": row.id, "topic_title": topic_row.title, "format": row.format,
            "status": row.status, "winner": row.winner, "created_at": iso_cn(row.created_at),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{debate_id}")
async def get_debate(debate_id: int, user=Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    debate = await _owned_debate(session, debate_id, user.id)
    topic_row = await _load_topic_row(session, debate.topic_id)
    card_rows = (await session.execute(
        select(ScorecardRow).where(ScorecardRow.debate_id == debate.id)
    )).scalars().all()
    # v2.7 复盘泳道总结：按落库实录现算（与直播 SSE 同源函数），旧场次无需迁移
    speech_rows = (await session.execute(
        select(SpeechRow).where(SpeechRow.debate_id == debate.id).order_by(SpeechRow.seq)
    )).scalars().all()
    swimlane = None
    if speech_rows:
        from types import SimpleNamespace

        from ..engine.summary import build_swimlane

        # SimpleNamespace 包装：把 SpeechRow.fallacies 挂到实录对象上（Speech schema 本身不带谬误字段）
        transcript = [SimpleNamespace(
            seq=r.seq, phase=r.phase, phase_name=r.phase_name,
            speaker=r.speaker_role, camp=Camp(r.camp), text=r.text,
            side_notes=r.side_notes or [], interrupted=r.interrupted,
            fallacies=r.fallacies or [],
        ) for r in speech_rows]
        swimlane = build_swimlane(transcript, topic=topic_row.title)
    # 决胜依据：优先取落库元数据（v1.9），历史场次按同一优先级回算
    meta = (debate.cfg_json or {}).get("verdict_meta") or {}
    rule = meta.get("rule")
    if not rule:
        votes = debate.votes_json or {}
        if votes.get("pro", 0) != votes.get("con", 0):
            rule = "majority"
        elif _fmt_num(debate.team_total_pro) != _fmt_num(debate.team_total_con):
            rule = "team_total"
        elif debate.winner is None:
            rule = "unresolved"
        else:
            bests = [c.best_speaker for c in card_rows if c.best_speaker and not c.abstained]
            top = Counter(bests).most_common()
            rule = "best_speaker" if top and top[0][1] > (top[1][1] if len(top) > 1 else 0) else "final_judge"
    return {
        "id": debate.id,
        "topic": {
            "id": topic_row.id, "title": topic_row.title,
            "pro_stance": topic_row.pro_stance or "",
            "con_stance": topic_row.con_stance or "",
            "pro_points": [p for p in (topic_row.pro_points or "").split("\n") if p.strip()],
            "con_points": [p for p in (topic_row.con_points or "").split("\n") if p.strip()],
        },
        "format": debate.format, "status": debate.status, "cfg": debate.cfg_json,
        "winner": debate.winner, "best_speaker": debate.best_speaker,
        "team_totals": {"pro": _fmt_num(debate.team_total_pro), "con": _fmt_num(debate.team_total_con)},
        "votes": debate.votes_json, "usage": debate.usage_json, "error": debate.error,
        "decisive_rule": rule, "final_reason": meta.get("reason"),
        "scorecards": [{
            "judge": c.judge, "focus": c.focus, "vote": c.vote,
            "best_speaker": c.best_speaker, "abstained": c.abstained,
            # v2.6：弃权但带点评 = 系统按实录+质检标注兜底生成（与引擎的 card.rescued 同义）
            "rescued": bool(c.abstained and (c.highlights_json or c.weaknesses_json)),
            "pro_total": sum((c.scores_json or {}).get("pro_scores", {}).values()),
            "con_total": sum((c.scores_json or {}).get("con_scores", {}).values()),
            "pro_scores": (c.scores_json or {}).get("pro_scores", {}),
            "con_scores": (c.scores_json or {}).get("con_scores", {}),
            "highlights": c.highlights_json or {},
            "weaknesses": c.weaknesses_json or {},
        } for c in card_rows],
        "started_at": iso_cn(debate.started_at), "finished_at": iso_cn(debate.finished_at),
        "created_at": iso_cn(debate.created_at),
        "swimlane": swimlane,
        # v2.9 金句摘录（终态随 cfg_json 落库；直播中由 SSE golden_quote 事件实时推送）
        "golden_quotes": (debate.cfg_json or {}).get("golden_quotes") or [],
    }


@router.get("/{debate_id}/speeches")
async def list_speeches(debate_id: int, after_seq: int = 0, limit: int = 200,
                        user=Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    await _owned_debate(session, debate_id, user.id)
    limit = min(max(limit, 1), 500)
    rows = (await session.execute(
        select(SpeechRow).where(SpeechRow.debate_id == debate_id, SpeechRow.seq > after_seq)
        .order_by(SpeechRow.seq).limit(limit)
    )).scalars().all()
    items = [{
        "seq": r.seq, "phase": r.phase, "phase_name": r.phase_name,
        "speaker": r.speaker_role, "camp": r.camp, "text": r.text,
        "side_notes": r.side_notes or [], "interrupted": r.interrupted,
        "word_count": r.word_count, "tokens": r.tokens,
        "fallacies": r.fallacies or [],
    } for r in rows]
    return {"items": items, "has_more": len(items) == limit}


@router.post("/{debate_id}/stop")
async def stop_debate(debate_id: int, request: Request, user=Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    state: AppState = get_state(request)
    debate = await _owned_debate(session, debate_id, user.id)
    if debate.status not in ("pending", "running"):
        raise ApiError(409, "DEBATE_NOT_RUNNING", "比赛不在进行中")
    # 立即改状态 + 置取消旗标；引擎在节点边界退出后 finalize 仍写 stopped
    # （ISS-011：状态写入用条件更新——读到 pending/running 与写入之间引擎可能恰好
    # 终态落库，条件更新保证终态 finished 不会被迟到的 stopped 回写覆盖）
    result = await session.execute(
        update(DebateRow)
        .where(DebateRow.id == debate_id, DebateRow.status.in_(("pending", "running")))
        .values(status="stopped")
    )
    if result.rowcount == 0:  # 条件更新未命中：终态先到，按不可停止处理
        await session.rollback()
        raise ApiError(409, "DEBATE_NOT_RUNNING", "比赛不在进行中")
    state.service.request_cancel(debate_id)
    return {"status": "stopped"}


async def _purge_debates(session: AsyncSession, ids: list[int]) -> int:
    """级联删除场次及其发言/评分卡（存档清理唯一入口，保证不留孤儿行）。"""
    if not ids:
        return 0
    await session.execute(delete(ScorecardRow).where(ScorecardRow.debate_id.in_(ids)))
    await session.execute(delete(SpeechRow).where(SpeechRow.debate_id.in_(ids)))
    result = await session.execute(delete(DebateRow).where(DebateRow.id.in_(ids)))
    return result.rowcount or 0


@router.delete("/{debate_id}", status_code=204)
async def delete_debate(debate_id: int, user=Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    """删除单场历史归档（连同发言、评分卡）。

    进行中/等待中的场次必须先终止：正在跑的引擎仍会向该 debate_id 写库，
    直接删除会立刻被 finalize 重新插回（甚至撞唯一键），所以这里明确拒绝。
    """
    debate = await _owned_debate(session, debate_id, user.id)
    if debate.status in ("pending", "running"):
        raise ApiError(409, "DEBATE_ACTIVE", "比赛仍在进行中，请先终止比赛再删除")
    session.expunge(debate)  # 交给批量 DELETE，避免 ORM 再发一次过期刷新
    await _purge_debates(session, [debate_id])
    await session.commit()
    return None


@router.delete("", status_code=200)
async def clear_archived_debates(user=Depends(get_current_user),
                                 session: AsyncSession = Depends(get_session)):
    """清空历史归档：删除本人全部已结束/已失败/已停止的场次（进行中不动）。"""
    ids = list((await session.execute(
        select(DebateRow.id).where(
            DebateRow.owner_id == user.id,
            DebateRow.status.in_(["finished", "failed", "stopped"]),
        )
    )).scalars().all())
    deleted = await _purge_debates(session, [int(i) for i in ids])
    await session.commit()
    return {"deleted": deleted}


@router.get("/{debate_id}/export")
async def export_debate(debate_id: int, request: Request, format: str = "md",
                        user=Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    state: AppState = get_state(request)
    if format not in ("md", "html", "xlsx"):
        raise ApiError(422, "VALIDATION_ERROR", "format 仅允许 md / html / xlsx")
    debate = await _owned_debate(session, debate_id, user.id)
    if debate.status != "finished":
        raise ApiError(409, "EXPORT_NOT_READY", "比赛尚未结束，无法导出")
    topic_row = await _load_topic_row(session, debate.topic_id)
    engine_topic = _row_to_engine_topic(topic_row)
    fmt = DebateFormat(debate.format)
    result = await _result_from_rows(session, debate)
    if format == "md":
        content = render_transcript_md(engine_topic, fmt, result, state.config)
        media_type = "text/markdown"
        filename = f"debate-{debate.id}-transcript.md"
    elif format == "html":
        content = render_report_html(engine_topic, fmt, result, state.config)
        media_type = "text/html"
        filename = f"debate-{debate.id}-report.html"
    else:
        content = render_scorecard_xlsx(engine_topic, fmt, result, state.config)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"debate-{debate.id}-scorecard.xlsx"
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _sse(event: dict) -> str:
    lines = []
    if event.get("id") is not None:
        lines.append(f"id: {event['id']}")
    lines.append(f"event: {event['event']}")
    lines.append(f"data: {json.dumps(event['data'], ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


@router.get("/{debate_id}/stream")
async def stream_debate(debate_id: int, request: Request, after_seq: int = 0,
                        user=Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    state: AppState = get_state(request)
    debate = await _owned_debate(session, debate_id, user.id)
    bus = state.service.get_bus(debate_id)
    terminal = debate.status in ("finished", "failed", "stopped")

    async def event_stream():
        # 1) 先订阅（若进行中），再重放 DB——重叠部分按 seq 去重，杜绝断线丢事件（ARCH A2）
        live_queue = bus.subscribe() if (bus is not None and not terminal and not bus.closed) else None
        try:
            last_seq = after_seq
            current_status, current_error = debate.status, debate.error
            own_session = state.sessionmaker()
            try:
                rows = (await own_session.execute(
                    select(SpeechRow).where(SpeechRow.debate_id == debate_id, SpeechRow.seq > after_seq)
                    .order_by(SpeechRow.seq)
                )).scalars().all()
                # ISS-012 BE-7：从入口读状态到重放完 DB 之间，引擎可能恰好收尾。
                # 重读一次再决定快照，避免把陈旧 running 发给刚重连的客户端后流就结束。
                latest = (await own_session.execute(
                    select(DebateRow.status, DebateRow.error).where(DebateRow.id == debate_id)
                )).first()
                if latest is not None:
                    current_status, current_error = latest[0], latest[1]
            finally:
                await own_session.close()
            for row in rows:
                last_seq = row.seq
                yield _sse({"event": "speech", "id": row.seq, "data": {
                    "seq": row.seq, "speaker": row.speaker_role, "camp": row.camp,
                    "text": row.text, "interrupted": row.interrupted,
                    "side_notes": list(row.side_notes or []),
                    "word_count": row.word_count, "phase": row.phase, "phase_name": row.phase_name,
                }})
            # 2) 快照：终态（含重读后刚终态）/ 总线已关 → 直接收尾；进行中补 status + 最近 phase_start
            terminal_now = current_status in ("finished", "failed", "stopped")
            if terminal_now or bus is None or live_queue is None or bus.closed:
                yield _sse({"event": "status", "id": (bus.allocate_id() if bus else None),
                            "data": {"status": current_status, "error": current_error}})
                return
            yield _sse({"event": "status", "id": bus.allocate_id(),
                        "data": {"status": current_status, "error": None}})
            if bus.last_phase_start:
                yield _sse({"event": "phase_start", "id": bus.allocate_id(),
                            "data": bus.last_phase_start})
            # v2.7：重连补当前时刻的复盘泳道总结
            if bus.last_swimlane:
                yield _sse({"event": "swimlane", "id": bus.allocate_id(),
                            "data": bus.last_swimlane})
            # v2.9：重连补发已累计的金句（前端按 quote 文本去重，全量补发幂等）
            if bus.golden_quotes:
                yield _sse({"event": "golden_quote", "id": bus.allocate_id(),
                            "data": {"quotes": list(bus.golden_quotes)}})
            # v2.9.4 修复（BE-P2-2）：谬误标注与金句对称补发——此前 fallacy 只以增量事件
            # 推送且落库在终态，重连客户端会永久丢失断线前的全部标注
            if bus.fallacy_marks:
                yield _sse({"event": "fallacy", "id": bus.allocate_id(),
                            "data": {"marks": list(bus.fallacy_marks)}})
            # 3) 实时消费
            while True:
                try:
                    event = await asyncio.wait_for(live_queue.get(), timeout=state.sse_heartbeat)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # 心跳保活（CONSTRAINTS F2）
                    continue
                kind = event["event"]
                if kind == "speech":
                    if event["data"]["seq"] <= last_seq:
                        continue
                    last_seq = event["data"]["seq"]
                yield _sse(event)
                if kind == "status" and event["data"].get("status") in ("finished", "failed", "stopped"):
                    return
        finally:
            if live_queue is not None and bus is not None:
                bus.unsubscribe(live_queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


async def _result_from_rows(session: AsyncSession, debate: DebateRow) -> DebateResult:
    """从 DB 行重建 DebateResult 形状，复用引擎渲染层（ARCH A4）。"""
    speech_rows = (await session.execute(
        select(SpeechRow).where(SpeechRow.debate_id == debate.id).order_by(SpeechRow.seq)
    )).scalars().all()
    transcript = [Speech(
        seq=r.seq, phase=r.phase, phase_name=r.phase_name, speaker=r.speaker_role,
        camp=Camp(r.camp), text=r.text, side_notes=list(r.side_notes or []),
        interrupted=r.interrupted, word_count=r.word_count, tokens=r.tokens,
    ) for r in speech_rows]

    card_rows = (await session.execute(
        select(ScorecardRow).where(ScorecardRow.debate_id == debate.id)
    )).scalars().all()
    scorecards = [ScoreCard(
        judge=r.judge, focus=r.focus,
        pro_scores=r.scores_json.get("pro_scores", {}),
        con_scores=r.scores_json.get("con_scores", {}),
        vote=Camp(r.vote) if r.vote else None, best_speaker=r.best_speaker,
        highlights={Camp.PRO: (r.highlights_json or {}).get("pro", []),
                    Camp.CON: (r.highlights_json or {}).get("con", [])},
        weaknesses={Camp.PRO: (r.weaknesses_json or {}).get("pro", []),
                    Camp.CON: (r.weaknesses_json or {}).get("con", [])},
        abstained=r.abstained,
    ) for r in card_rows]

    votes_raw = debate.votes_json or {}
    votes = {Camp.PRO: int(votes_raw.get("pro", 0)), Camp.CON: int(votes_raw.get("con", 0))}
    totals = {Camp.PRO: float(debate.team_total_pro or 0), Camp.CON: float(debate.team_total_con or 0)}
    meta = (debate.cfg_json or {}).get("verdict_meta") or {}
    rule = meta.get("rule")
    if not rule:
        if votes[Camp.PRO] != votes[Camp.CON]:
            rule = "majority"
        elif totals[Camp.PRO] != totals[Camp.CON]:
            rule = "team_total"
        else:
            rule = "best_speaker" if debate.winner else "unresolved"
    verdict = Verdict(
        votes=votes, team_totals=totals,
        winner=Camp(debate.winner) if debate.winner else None,
        best_speaker=debate.best_speaker, decisive_rule=rule,
        final_reason=meta.get("reason"),
    )
    return DebateResult(status=debate.status, transcript=transcript,
                        scorecards=scorecards, verdict=verdict)
