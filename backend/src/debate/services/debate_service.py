"""DebateService（ARCH A1）：引擎作为后台线程任务运行，发言流式落库，终态回写。

- 引擎是同步阻塞代码（graph.invoke + 同步 LLM 调用），经 asyncio.to_thread 放入线程池，
  不阻塞事件循环；
- 发言经 call_soon_threadsafe 进入队列，由事件循环内的消费任务写库（SSE 直播 M4 挂同一出口）；
- 终态（finished/stopped/failed）时按最终 transcript 全量 upsert——质询打断会回改先前发言，
  以引擎结果为准保证 DB 是唯一事实来源（CONSTRAINTS B5）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import func, select

from ..core.config import AppConfig
from ..db.models import DebateRow, ScorecardRow, SpeechRow
from ..engine.agents.loader import AgentSpec
from ..engine.llm.gateway import Gateway
from ..engine.runner import DebateResult, run_debate
from ..engine.schema import CAMP_CN, Camp, DebateFormat, Speech, Topic
from .event_bus import EventBus

logger = logging.getLogger("debate.service")

RUNNING_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("finished", "failed", "stopped")


class QuotaExceeded(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


class DebateService:
    def __init__(
        self,
        *,
        config: AppConfig,
        agents: dict[str, AgentSpec],
        sessionmaker: async_sessionmaker,
        gateway_factory: Callable[[AppConfig, DebateFormat], Gateway],
        compressor_factory: Callable[[AppConfig, Gateway], Callable],
        now_fn: Callable[[], object],
    ):
        self.config = config
        self.agents = agents
        self.sessionmaker = sessionmaker
        self.gateway_factory = gateway_factory
        self.compressor_factory = compressor_factory
        self.now_fn = now_fn
        self._tasks: dict[int, asyncio.Task] = {}
        self._cancel_flags: dict[int, dict] = {}
        self._buses: dict[int, EventBus] = {}

    # ---- 配额（CONSTRAINTS A5：并发 1 场 + 东八区自然日配额） ----

    async def check_quota(self, owner_id: int) -> None:
        """v1.3 修订：仅并发限制（同时 1 场）；每日场次不限。"""
        async with self.sessionmaker() as session:
            running = await session.execute(
                select(func.count()).select_from(DebateRow).where(
                    DebateRow.owner_id == owner_id, DebateRow.status.in_(RUNNING_STATUSES)
                )
            )
            if (running.scalar_one() or 0) >= self.config.quota.concurrent_debates_per_user:
                raise QuotaExceeded("QUOTA_CONCURRENT", "已有进行中的比赛，请稍后再试")

    # ---- 生命周期 ----

    def get_bus(self, debate_id: int) -> EventBus | None:
        return self._buses.get(debate_id)

    def start(self, *, debate_id: int, topic: Topic, fmt: DebateFormat) -> None:
        self._buses[debate_id] = EventBus(debate_id)  # 先建总线再起任务，杜绝订阅空窗
        gateway = self.gateway_factory(self.config, fmt)
        compressor = self.compressor_factory(self.config, gateway)
        flag = {"cancelled": False}
        self._cancel_flags[debate_id] = flag
        task = asyncio.create_task(self._job(debate_id=debate_id, topic=topic, fmt=fmt, gateway=gateway,
                                             compressor=compressor, cancel_flag=flag))
        self._tasks[debate_id] = task
        task.add_done_callback(lambda _t: (self._tasks.pop(debate_id, None), self._cancel_flags.pop(debate_id, None)))

    def request_cancel(self, debate_id: int) -> bool:
        """协作式取消（CONSTRAINTS V：stop 只改旗标，引擎在节点边界退出）。"""
        flag = self._cancel_flags.get(debate_id)
        if flag is None:
            return False
        flag["cancelled"] = True
        return True

    async def shutdown(self) -> None:
        for flag in self._cancel_flags.values():
            flag["cancelled"] = True
        tasks = [t for t in self._tasks.values() if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- 内部 ----

    async def _mark(self, debate_id: int, **fields) -> None:
        async with self.sessionmaker() as session:
            debate = await session.get(DebateRow, debate_id)
            for key, value in fields.items():
                setattr(debate, key, value)
            session.add(debate)
            await session.commit()

    async def _job(self, *, debate_id: int, topic: Topic, fmt: DebateFormat,
                   gateway: Gateway, compressor: Callable, cancel_flag: dict) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        bus = self._buses.get(debate_id)

        def on_speech(speech: Speech) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, speech)
            if bus is not None:
                loop.call_soon_threadsafe(bus.publish, {
                    "event": "speech", "id": speech.seq,
                    "data": {
                        "seq": speech.seq, "speaker": speech.speaker, "camp": speech.camp.value,
                        "text": speech.text, "interrupted": speech.interrupted,
                        "side_notes": list(speech.side_notes),
                        "word_count": speech.word_count,  # v2.1 增补：前端动态计时条需要
                    },
                })

        def on_event(event: dict) -> None:
            if bus is not None:
                loop.call_soon_threadsafe(bus.publish, dict(event))

        def on_delta(seq: int, delta: str, attempt: int, speaker: str, camp: str) -> None:
            """逐字流式（v2.1 speech_delta）：仅直播通道，不落库不重放。"""
            if bus is not None and self.config.stream.enable_delta:
                loop.call_soon_threadsafe(bus.publish, {
                    "event": "speech_delta",
                    "data": {"seq": seq, "delta": delta, "attempt": attempt,
                             "speaker": speaker, "camp": camp},
                })

        await self._mark(debate_id, status="running", started_at=self.now_fn())
        if bus is not None:
            bus.publish({"event": "status", "data": {"status": "running", "error": None}})

        def engine_callable() -> DebateResult:
            return run_debate(
                topic=topic, fmt=fmt, config=self.config, gateway=gateway, compressor=compressor,
                agents=self.agents, on_speech=on_speech, on_event=on_event,
                on_delta=on_delta if self.config.stream.enable_delta else None,
                cancel_check=lambda: cancel_flag["cancelled"],
            )

        drain_task = asyncio.create_task(self._drain(queue, debate_id))
        try:
            result = await asyncio.to_thread(engine_callable)
        except Exception as exc:  # noqa: BLE001 —— 任何引擎异常都落 failed（ARCH §13）
            logger.exception("debate %s crashed", debate_id)
            await self._mark(debate_id, status="failed", error=str(exc), finished_at=self.now_fn())
            if bus is not None:
                bus.publish({"event": "status", "data": {"status": "failed", "error": str(exc)}})
                bus.close()
            return
        finally:
            await queue.join()
            drain_task.cancel()
        await self._finalize(debate_id, result)
        if bus is not None:
            bus.publish({"event": "status", "data": {"status": result.status, "error": result.error}})
            bus.close()

    async def _drain(self, queue: asyncio.Queue, debate_id: int) -> None:
        while True:
            speech: Speech = await queue.get()
            try:
                async with self.sessionmaker() as session:
                    session.add(SpeechRow(
                        debate_id=debate_id, seq=speech.seq, phase=speech.phase,
                        phase_name=speech.phase_name, speaker_role=speech.speaker,
                        camp=speech.camp.value, text=speech.text,
                        side_notes=list(speech.side_notes), interrupted=speech.interrupted,
                        word_count=speech.word_count, tokens=speech.tokens,
                    ))
                    await session.commit()
            finally:
                queue.task_done()

    async def _finalize(self, debate_id: int, result: DebateResult) -> None:
        async with self.sessionmaker() as session:
            # 1) 按引擎最终 transcript 全量 upsert（质询打断会回改先前发言）
            for speech in result.transcript:
                row = (await session.execute(
                    select(SpeechRow).where(SpeechRow.debate_id == debate_id, SpeechRow.seq == speech.seq)
                )).scalar_one_or_none()
                if row is None:
                    row = SpeechRow(debate_id=debate_id, seq=speech.seq)
                    session.add(row)
                row.phase = speech.phase
                row.phase_name = speech.phase_name
                row.speaker_role = speech.speaker
                row.camp = speech.camp.value
                row.text = speech.text
                row.side_notes = list(speech.side_notes)
                row.interrupted = speech.interrupted
                row.word_count = speech.word_count
                row.tokens = speech.tokens

            # 2) 评分卡
            debate = await session.get(DebateRow, debate_id)
            debate.status = result.status
            debate.error = result.error
            debate.finished_at = self.now_fn()
            debate.usage_json = dict(result.usage)
            if result.verdict:
                debate.winner = result.verdict.winner.value if result.verdict.winner else None
                debate.best_speaker = result.verdict.best_speaker
                debate.team_total_pro = result.verdict.team_totals[Camp.PRO]
                debate.team_total_con = result.verdict.team_totals[Camp.CON]
                debate.votes_json = {k.value: v for k, v in result.verdict.votes.items()}
            for card in result.scorecards:
                session.add(ScorecardRow(
                    debate_id=debate_id, judge=card.judge, focus=card.focus,
                    scores_json={"pro_scores": card.pro_scores, "con_scores": card.con_scores},
                    vote=card.vote.value if card.vote else None,
                    best_speaker=card.best_speaker,
                    highlights_json={k.value: v for k, v in card.highlights.items()},
                    weaknesses_json={k.value: v for k, v in card.weaknesses.items()},
                    abstained=card.abstained,
                ))
            session.add(debate)
            await session.commit()
