"""服务层回归（v1.9.2）：引擎跑完后场次必须收敛为终态，前端不得永久停在"评委合议中"。

历史缺陷（用户报告的"评分环节卡住"直接成因）：
`DebateService._finalize` 里 `marks_by_seq` 在写库循环里被先读、后在循环之后才赋值 →
每次收尾必抛 `UnboundLocalError`；而 `_finalize` 当时在 `try/except` 之外，
异常逃出 `_job` → EventBus 既不关闭也不发 `status` 终态 → SSE 一直挂着，
前端 `judging` 永远为真。此文件锁死"终态必达"这条不变量。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from debate.core.config import AppConfig
from debate.db.models import DebateRow, GraphSnapshotRow, ScorecardRow, SpeechRow
from debate.engine.graph.debate_state import GraphTracker
from debate.engine.llm.gateway import Gateway
from debate.engine.runner import DebateResult
from debate.engine.schema import Camp, DebateFormat, ScoreCard, Speech, Topic, Verdict
from debate.services import debate_service as service_module
from debate.services.debate_service import DebateService
from debate.services.event_bus import EventBus

TOPIC = Topic(
    topic_id="t-reg", title="人工智能时代，人类应该更依赖技术 / 更保持警惕",
    pro_stance="更依赖技术", con_stance="更保持警惕",
    pro_points=["效率"], con_points=["自主"],
)


def _speech(seq: int, speaker: str, camp: Camp, text: str, phase: int = 2,
            phase_name: str = "开篇立论") -> Speech:
    return Speech(seq=seq, phase=phase, phase_name=phase_name, speaker=speaker,
                  camp=camp, text=text, word_count=len(text))


def _card(judge: str = "judge-logic") -> ScoreCard:
    return ScoreCard(
        judge=judge, focus="logic",
        pro_scores={"statement": 26, "rebuttal_cross": 30, "free": 16, "closing": 13, "conduct": 5},
        con_scores={"statement": 24, "rebuttal_cross": 28, "free": 15, "closing": 12, "conduct": 4},
        vote=Camp.PRO, best_speaker="pro-1",
        highlights={Camp.PRO: ["“依赖是能力延伸”"], Camp.CON: ["“安全阀”提法"]},
        weaknesses={Camp.PRO: [], Camp.CON: ["“警惕导致停滞”举证不足"]},
    )


def _result() -> DebateResult:
    return DebateResult(
        status="finished",
        transcript=[
            _speech(1, "chair", Camp.NEUTRAL, "本场辩题是……", phase=1, phase_name="主席开场"),
            _speech(2, "pro-1", Camp.PRO, "我方认为更依赖技术。"),
            _speech(3, "con-1", Camp.CON, "我方认为更应保持警惕。"),
        ],
        scorecards=[_card()],
        verdict=Verdict(votes={Camp.PRO: 2, Camp.CON: 1},
                        team_totals={Camp.PRO: 90.0, Camp.CON: 83.0},
                        winner=Camp.PRO, best_speaker="pro-1", decisive_rule="majority"),
        machine_marks=[{"seq": 2, "camp": "pro", "speaker": "pro-1", "name": "滑坡谬误",
                        "quote": "依赖必然导致", "explanation": "…", "confidence": "high"}],
        usage={"calls": 1},
    )


async def _make(db_path) -> tuple[DebateService, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    svc = DebateService(
        config=AppConfig(), agents={}, sessionmaker=maker,
        gateway_factory=lambda cfg, fmt: Gateway(backend=None, model_debater="x", model_official="x"),
        compressor_factory=lambda cfg, gw: (lambda speeches: {}),
        now_fn=lambda: __import__("datetime").datetime(2026, 10, 27, 12, 0, 0),
    )
    async with maker() as session:
        session.add(DebateRow(id=1, topic_id=1, owner_id=1, format="full", status="pending"))
        await session.commit()
    return svc, maker


async def _run_job(db_path, monkeypatch, engine_impl=None):
    svc, maker = await _make(db_path)
    impl = engine_impl or (lambda **kw: _result())
    monkeypatch.setattr(service_module, "run_debate", impl)
    svc.start(debate_id=1, topic=TOPIC, fmt=DebateFormat.FULL)
    bus = svc.get_bus(1)
    queue = bus.subscribe()  # start() 已建总线；任务要等下一个 await 才跑，此处订阅不漏事件
    await asyncio.wait_for(svc._tasks[1], timeout=20)
    events: list[dict] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return bus, events, maker


def test_finalize_persists_fallacies_and_terminal_status(tmp_path, monkeypatch):
    """回归：收尾必须正常落库（谬误挂载到发言），且总线发出 finished 终态。"""
    async def main():
        bus, events, maker = await _run_job(tmp_path / "reg.db", monkeypatch)
        # 1) 总线必须发出终态（此前因 UnboundLocalError 永不发出）
        statuses = [e["data"]["status"] for e in events if e["event"] == "status"]
        assert statuses[-1] == "finished", statuses
        assert bus.closed is True
        # 2) 落库：发言带谬误标注，评分卡与终态齐全
        async with maker() as session:
            rows = (await session.execute(select(SpeechRow).order_by(SpeechRow.seq))).scalars().all()
            assert [r.seq for r in rows] == [1, 2, 3]
            assert rows[1].fallacies and rows[1].fallacies[0]["name"] == "滑坡谬误"
            assert rows[0].fallacies == []
            card_rows = (await session.execute(select(ScorecardRow))).scalars().all()
            assert len(card_rows) == 1
            debate = await session.get(DebateRow, 1)
            assert debate.status == "finished"
            assert debate.winner == "pro"
            assert (debate.cfg_json or {}).get("verdict_meta", {}).get("rule") == "majority"

    asyncio.run(main())


def test_graph_snapshots_are_persisted_for_playback(tmp_path, monkeypatch):
    """v2.5：逐轮图快照必须在终态落库，归档回看才能逐回合翻看。

    直播期间图走 SSE，不读库；这里锁死"刷新/回看也拿得到历史图"这条。
    """

    def with_graph() -> DebateResult:
        result = _result()
        tracker = GraphTracker(topic=TOPIC.title)
        for speech in result.transcript:
            tracker.feed_speech(speech)
        tracker.feed_phase_end(2)
        tracker.feed_verdict(winner="pro", rule="majority")
        result.graph_snapshots = tracker.history
        return result

    async def main():
        _bus, _events, maker = await _run_job(tmp_path / "graph.db", monkeypatch,
                                              lambda **kw: with_graph())
        async with maker() as session:
            rows = (await session.execute(
                select(GraphSnapshotRow).order_by(GraphSnapshotRow.version)
            )).scalars().all()
            # 3 条发言 + 1 次环节结束 + 1 次宣判
            assert [r.version for r in rows] == [1, 2, 3, 4, 5]
            assert rows[-1].argument.startswith("flowchart LR")
            assert rows[-1].payload_json["terminated"] is True
            # 版本 1 的图里只有主席开场，版本 3 之后才长出双方论点——历史不是同一张图
            assert ":::neutral" in rows[0].argument
            assert ":::pro" not in rows[0].argument
            assert ":::con" in rows[2].argument

    asyncio.run(main())


def test_duplicate_seq_transcript_is_deduped_before_write(tmp_path, monkeypatch):
    """回归（v2.2 用户现场）：transcript 出现重复 seq 时，收尾必须去重后写入，
    不能撞 `speeches.uq_debate_seq`——此前 MySQL 抛 1062，整场被判 failed，
    前端显示"评审未能完成：(1062, Duplicate entry ...)"。"""

    def dupe_result() -> DebateResult:
        result = _result()
        # 模拟引擎重复 append（宣判/补写场景）：同一 seq 出现两次，后写覆盖先写
        result.transcript = [
            *result.transcript,
            _speech(2, "pro-1", Camp.PRO, "补写版本：我方仍主张更依赖技术。"),
        ]
        return result

    async def main():
        bus, events, maker = await _run_job(tmp_path / "dupe.db", monkeypatch, lambda **kw: dupe_result())
        statuses = [e["data"]["status"] for e in events if e["event"] == "status"]
        assert statuses[-1] == "finished", statuses
        async with maker() as session:
            rows = (await session.execute(select(SpeechRow).order_by(SpeechRow.seq))).scalars().all()
            assert [r.seq for r in rows] == [1, 2, 3]  # 唯一键不冲突
            assert rows[1].text == "补写版本：我方仍主张更依赖技术。"  # 后写覆盖

    asyncio.run(main())


def test_job_crash_still_publishes_terminal_status(tmp_path, monkeypatch):
    """引擎抛异常时也必须发 failed 终态并关闭总线（前端不会卡在"合议中"）。"""
    def boom(**kwargs):
        raise RuntimeError("引擎炸了")

    async def main():
        bus, events, _maker = await _run_job(tmp_path / "boom.db", monkeypatch, boom)
        statuses = [e["data"] for e in events if e["event"] == "status"]
        assert statuses[-1]["status"] == "failed"
        assert "引擎炸了" in (statuses[-1]["error"] or "")
        assert bus.closed is True

    asyncio.run(main())


def test_finalize_failure_marks_debate_failed(tmp_path, monkeypatch):
    """收尾写库失败时，场次必须显式落 failed（而不是静默停留在 running）。"""
    async def main():
        svc, maker = await _make(tmp_path / "fin.db")
        monkeypatch.setattr(service_module, "run_debate", lambda **kw: _result())

        async def bad_finalize(debate_id, result):
            raise RuntimeError("落库失败")

        monkeypatch.setattr(svc, "_finalize", bad_finalize)
        svc.start(debate_id=1, topic=TOPIC, fmt=DebateFormat.FULL)
        bus = svc.get_bus(1)
        queue = bus.subscribe()
        await asyncio.wait_for(svc._tasks[1], timeout=20)

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert [e["data"]["status"] for e in events if e["event"] == "status"][-1] == "failed"
        async with maker() as session:
            debate = await session.get(DebateRow, 1)
            assert debate.status == "failed"
            assert "落库失败" in (debate.error or "")

    asyncio.run(main())
