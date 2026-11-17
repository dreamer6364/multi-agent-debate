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
import time
from dataclasses import dataclass, field, replace as _dc_replace
from typing import Callable

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import func, select

from ..core.config import AppConfig
from ..db.models import DebateRow, ScorecardRow, SpeechRow
from ..engine.agents.loader import AgentSpec
from ..engine.llm.gateway import Gateway
from ..engine.llm.observability import new_trace_id
from ..engine.runner import DebateResult, run_debate
from ..engine.schema import Camp, DebateFormat, Speech, Topic
from .event_bus import EventBus

logger = logging.getLogger("debate.service")

RUNNING_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("finished", "failed", "stopped")

BUS_REPLAY_TTL_SECONDS = 60.0    # 终态后保留总线，供窄窗口重连补发（ISS-012 BE-2）
QUOTA_LOCK_TTL_SECONDS = 300.0  # 空闲配额锁的回收时限（ISS-012 BE-2）


@dataclass
class _QuotaEntry:
    """按用户的配额锁 + 在途 guard 计数；只有 users==0 且锁空闲时才可安全回收。"""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    last_used: float = 0.0


class _QuotaGuard:
    """`async with service.quota_guard(uid)` 的代理：持有期间阻止该 entry 被回收。

    直接 pop 一个 asyncio.Lock 是错的——已拿到旧锁、尚未进入临界区的协程会与
    新协程创建的新锁并存，配额串行化被悄悄破坏（ISS-012 BE-2）。
    """
    def __init__(self, service: "DebateService", owner_id: int, entry: _QuotaEntry):
        self._service = service
        self._owner_id = owner_id
        self._entry = entry

    async def __aenter__(self) -> "_QuotaGuard":
        try:
            await self._entry.lock.acquire()
        except BaseException:
            self._entry.users -= 1  # 等锁被取消/异常：不留永久在途计数
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._entry.lock.release()
        self._entry.users -= 1
        self._service._prune_quota_locks()
        return False


class QuotaExceeded(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


class EngineTimeout(RuntimeError):
    """引擎超过总时限且宽限期内仍未收束——服务层按 failed 收尾，绝不让场次卡在 running。"""


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
        self._quota_locks: dict[int, _QuotaEntry] = {}  # 按用户串行化配额闸（ISS-001 / BE-2 可回收）

    # ---- 配额（CONSTRAINTS A5：并发 1 场 + 东八区自然日配额） ----

    async def check_quota(self, owner_id: int) -> None:
        """v1.3 修订：仅并发限制（同时 1 场）；每日场次不限。

        注意：本方法只读不锁，调用方必须持有 `quota_guard(owner_id)` 临界区，
        否则并发创建会双双通过检查（ISS-001 TOCTOU）。
        """
        async with self.sessionmaker() as session:
            running = await session.execute(
                select(func.count()).select_from(DebateRow).where(
                    DebateRow.owner_id == owner_id, DebateRow.status.in_(RUNNING_STATUSES)
                )
            )
            if (running.scalar_one() or 0) >= self.config.quota.concurrent_debates_per_user:
                raise QuotaExceeded("QUOTA_CONCURRENT", "已有进行中的比赛，请稍后再试")

    def quota_guard(self, owner_id: int) -> _QuotaGuard:
        """创建比赛的配额临界区（ISS-001）：check_quota 与落库必须同持一把锁。

        ARCH A1 单实例假设下进程内锁即关闭 check-then-act 窗口；
        水平扩展时需换 DB 行锁（SELECT ... FOR UPDATE）或分布式锁，
        与 EventBus 换 Redis 是同一时机的改造。
        """
        self._prune_quota_locks()
        entry = self._quota_locks.get(owner_id)
        if entry is None:
            entry = _QuotaEntry()
            self._quota_locks[owner_id] = entry
        entry.last_used = time.monotonic()
        entry.users += 1  # 从交出 guard 起即在途，关闭"拿到锁但未进临界区"的淘汰窗口
        return _QuotaGuard(self, owner_id, entry)

    def _prune_quota_locks(self) -> None:
        """回收长期空闲且无在途使用者的配额锁，避免按用户数无限增长。"""
        now = time.monotonic()
        stale = [uid for uid, entry in self._quota_locks.items()
                 if entry.users == 0 and not entry.lock.locked()
                 and now - entry.last_used > QUOTA_LOCK_TTL_SECONDS]
        for uid in stale:
            self._quota_locks.pop(uid, None)

    # ---- 生命周期 ----

    def get_bus(self, debate_id: int) -> EventBus | None:
        return self._buses.get(debate_id)

    def _schedule_bus_reclaim(self, debate_id: int, bus: EventBus) -> None:
        """ISS-012 BE-2：终态后延迟回收总线（含全部快照），而不是永久驻留。

        保留 60s 供「读到 running、订阅期间引擎恰好收尾」的窄窗口重连补发；
        之后 get_bus 返回 None，stream 端点已按 DB 终态收尾，功能不受影响。
        """
        def _evict() -> None:
            if self._buses.get(debate_id) is bus:  # 同 id 被复用的极端情况不误删新总线
                self._buses.pop(debate_id, None)

        try:
            asyncio.get_running_loop().call_later(BUS_REPLAY_TTL_SECONDS, _evict)
        except RuntimeError:  # 无事件循环（同步单测）：立刻回收即可
            _evict()

    def start(self, *, debate_id: int, topic: Topic, fmt: DebateFormat) -> None:
        bus = EventBus(debate_id)
        self._buses[debate_id] = bus  # 先建总线再起任务，杜绝订阅空窗
        gateway = self.gateway_factory(self.config, fmt)
        compressor = self.compressor_factory(self.config, gateway)
        flag = {"cancelled": False}
        self._cancel_flags[debate_id] = flag
        task = asyncio.create_task(self._job(debate_id=debate_id, topic=topic, fmt=fmt, gateway=gateway,
                                             compressor=compressor, cancel_flag=flag))
        self._tasks[debate_id] = task

        def _on_done(_t: asyncio.Task) -> None:
            self._tasks.pop(debate_id, None)
            self._cancel_flags.pop(debate_id, None)
            self._schedule_bus_reclaim(debate_id, bus)

        task.add_done_callback(_on_done)

    def request_cancel(self, debate_id: int) -> bool:
        """协作式取消（CONSTRAINTS V：stop 只改旗标，引擎在节点边界退出）。"""
        flag = self._cancel_flags.get(debate_id)
        if flag is None:
            return False
        flag["cancelled"] = True
        return True

    async def shutdown(self) -> None:
        """优雅停机：置取消旗标后有界等待——引擎线程只能在节点边界响应取消，
        若正卡在长 LLM 调用（客户端超时×重试可达数分钟），无界 gather 会把
        lifespan 关闭挂起同样长的时间。超时后放弃等待（线程随进程退出）。"""
        for flag in self._cancel_flags.values():
            flag["cancelled"] = True
        tasks = [t for t in self._tasks.values() if not t.done()]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=15.0,
                )
            except TimeoutError:
                pass  # 有界让步：未完成的任务不再阻塞停机

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
        trace_id = new_trace_id()  # ISS-014 A-5：本场追踪 id（LLM 日志 / SSE / 结果同 id）

        def on_speech(speech: Speech) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, speech)
            if bus is not None:
                loop.call_soon_threadsafe(bus.publish, {
                    "event": "speech", "id": speech.seq,
                    "data": {
                        "trace_id": trace_id,  # ISS-014 A-5：与 LLM 调用日志同 id
                        "seq": speech.seq, "speaker": speech.speaker, "camp": speech.camp.value,
                        "text": speech.text, "interrupted": speech.interrupted,
                        "side_notes": list(speech.side_notes),
                        "word_count": speech.word_count,  # v2.1 增补：前端动态计时条需要
                        # v2.3 增补：带上环节戳——重连/补发时前端不必依赖"当前 phase"去猜，
                        # 否则主席开篇（phase 1）在 phase_start 丢失的窗口里会被归到后续环节
                        "phase": speech.phase, "phase_name": speech.phase_name,
                    },
                })

        def on_event(event: dict) -> None:
            if bus is not None:
                event = dict(event)
                event.setdefault("data", {})["trace_id"] = trace_id  # ISS-014 A-5
                loop.call_soon_threadsafe(bus.publish, event)
            # P1-1 打断实时修正：该发言行多半已按"完整文本"插入（打断发生在下一条提问时），
            # 就地改成截断后文本——断线重连从 DB 重放时才是"已被打断"的正确终稿。
            # 行尚未插入时跳过：队列里的 Speech 是同一对象引用，drain 落库时已是截断文本。
            if event.get("event") == "interrupt" and event.get("data", {}).get("seq"):
                payload = dict(event["data"])
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._patch_interrupted(debate_id, payload))
                )

        def on_delta(seq: int, delta: str, attempt: int, speaker: str, camp: str) -> None:
            """逐字流式（v2.1 speech_delta）：仅直播通道，不落库不重放。"""
            if bus is not None and self.config.stream.enable_delta:
                loop.call_soon_threadsafe(bus.publish, {
                    "event": "speech_delta",
                    "data": {"trace_id": trace_id, "seq": seq, "delta": delta, "attempt": attempt,
                             "speaker": speaker, "camp": camp},
                })

        await self._mark(debate_id, status="running", started_at=self.now_fn())
        if bus is not None:
            bus.publish({"event": "status", "data": {"status": "running", "error": None,
                                                      "trace_id": trace_id}})

        def engine_callable() -> DebateResult:
            return run_debate(
                topic=topic, fmt=fmt, config=self.config, gateway=gateway, compressor=compressor,
                agents=self.agents, on_speech=on_speech, on_event=on_event,
                on_delta=on_delta if self.config.stream.enable_delta else None,
                cancel_check=lambda: cancel_flag["cancelled"], trace_id=trace_id,
            )

        drain_task = asyncio.create_task(self._drain(queue, debate_id))
        status, error = "failed", "引擎未产出结果"
        drained = False
        try:
            result = await self._run_engine(
                engine_callable, debate_id=debate_id, cancel_flag=cancel_flag, bus=bus
            )
            status, error = result.status, result.error
            # v2.2：**先把落库队列排空，再全量 upsert**。
            # 此前 finalize 与 drain 并发写同一批 (debate_id, seq)：drain 的 INSERT 与
            # finalize 的 SELECT→INSERT 抢同一个唯一键，MySQL 直接抛
            # "Duplicate entry 'x-y' for key 'speeches.uq_debate_seq'"，整场被判 failed，
            # 用户侧看到的就是"评审未能完成：(1062, Duplicate entry...)"。
            # 引擎线程此时已结束，不会再有新发言入队，先排空是安全的。
            await self._finish_drain(queue, drain_task, debate_id)
            drained = True
            # v1.9.2：_finalize 必须与引擎调用同处一个 try——此前 finalize 抛异常时
            # 任务直接崩掉，总线既不关闭也不发终态，前端永远停在"评委合议中"
            await self._finalize(debate_id, result)
        except Exception as exc:  # noqa: BLE001 —— 任何异常都必须收敛为终态（ARCH §13）
            logger.exception("debate %s crashed", debate_id)
            status, error = "failed", str(exc) or type(exc).__name__
            try:
                await self._mark(debate_id, status="failed", error=error, finished_at=self.now_fn())
            except Exception:  # noqa: BLE001 —— 终态回写失败也要继续播报，不能静默
                logger.exception("debate %s 终态回写失败", debate_id)
        finally:
            if not drained:
                await self._finish_drain(queue, drain_task, debate_id)
        if bus is not None:
            bus.publish({"event": "status", "data": {"status": status, "error": error,
                                                      "trace_id": trace_id}})
            bus.close()

    async def _run_engine(self, engine_callable: Callable[[], DebateResult], *, debate_id: int,
                          cancel_flag: dict, bus: EventBus | None) -> DebateResult:
        """引擎调用带总时限看门狗：超时置取消旗标 → 宽限 → 仍不返回则强制收敛为失败。"""
        cfg = self.config.engine
        overall = float(cfg.max_duration_seconds)
        grace = float(cfg.cancel_grace_seconds)
        task = asyncio.create_task(asyncio.to_thread(engine_callable))
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=overall)
        except asyncio.TimeoutError:
            logger.error("debate %s 超过总时限 %.0fs，请求引擎收束", debate_id, overall)
            cancel_flag["cancelled"] = True
            if bus is not None:
                bus.publish({"event": "error", "data": {
                    "message": f"本场运行已超过 {overall:.0f} 秒，正在强制收束",
                }})
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=grace)
            except asyncio.TimeoutError:
                task.cancel()  # 线程无法强杀，但拒绝继续等待
                raise EngineTimeout(
                    f"引擎超过总时限（{overall:.0f}s），宽限 {grace:.0f}s 内未收束，已强制结束"
                ) from None

    async def _finish_drain(self, queue: asyncio.Queue, drain_task: asyncio.Task, debate_id: int) -> None:
        """收尾排空：**有界等待**——drain 任务意外终止时 queue.join() 会永久挂起。"""
        try:
            await asyncio.wait_for(queue.join(), timeout=float(self.config.engine.drain_timeout_seconds))
        except asyncio.TimeoutError:
            logger.warning("debate %s 落库队列未在时限内排空（剩余 %s 条待处理）", debate_id, queue.qsize())
        except Exception:  # noqa: BLE001
            logger.exception("debate %s 落库队列排空异常", debate_id)
        drain_task.cancel()
        try:
            await drain_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    async def _drain(self, queue: asyncio.Queue, debate_id: int) -> None:
        """逐条落库：单条失败只记日志并跳过——任务一旦终止，queue.join() 将永远等不到。"""
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
            except Exception:  # noqa: BLE001 —— 落库失败不终止消费（终态会全量 upsert 兜底）
                logger.warning("debate %s 第 %s 条发言落库失败", debate_id, speech.seq, exc_info=True)
                try:  # 唯一键冲突等场景改为更新已存在行，避免该条在终态前一直是旧文本
                    await self._update_speech(debate_id, speech)
                except Exception:  # noqa: BLE001
                    logger.warning("debate %s 第 %s 条发言补写亦失败", debate_id, speech.seq, exc_info=True)
            finally:
                queue.task_done()

    async def _update_speech(self, debate_id: int, speech: Speech) -> None:
        async with self.sessionmaker() as session:
            row = (await session.execute(
                select(SpeechRow).where(SpeechRow.debate_id == debate_id, SpeechRow.seq == speech.seq)
            )).scalar_one_or_none()
            if row is None:
                return
            row.phase = speech.phase
            row.phase_name = speech.phase_name
            row.speaker_role = speech.speaker
            row.camp = speech.camp.value
            row.text = speech.text
            row.side_notes = list(speech.side_notes)
            row.interrupted = speech.interrupted
            row.word_count = speech.word_count
            row.tokens = speech.tokens
            session.add(row)
            await session.commit()

    async def _patch_interrupted(self, debate_id: int, payload: dict) -> None:
        """P1-1：打断回改的实时落库——把已插入的该条发言就地改为截断后终稿。

        行不存在时静默返回（发言尚未落库，drain 会写入已被引擎截断的同一对象）。
        任何失败只记日志：终态 `_write_final` 全量 upsert 兜底，不值得打断直播。
        """
        try:
            async with self.sessionmaker() as session:
                row = (await session.execute(
                    select(SpeechRow).where(
                        SpeechRow.debate_id == debate_id, SpeechRow.seq == payload["seq"])
                )).scalar_one_or_none()
                if row is None:
                    return
                row.text = payload.get("text", row.text)
                row.side_notes = list(payload.get("side_notes") or row.side_notes or [])
                row.interrupted = True
                row.word_count = payload.get("word_count", row.word_count)
                session.add(row)
                await session.commit()
        except Exception:  # noqa: BLE001 —— 实时修正尽力而为，终态 upsert 兜底
            logger.warning("debate %s 打断修正落库失败", debate_id, exc_info=True)

    async def _finalize(self, debate_id: int, result: DebateResult) -> None:
        """终态落库：撞唯一键时先清掉历史重复发言再重写一次，绝不让整场因此判失败。"""
        try:
            await self._write_final(debate_id, result)
        except IntegrityError:
            removed = await self._dedupe_speech_rows(debate_id)
            logger.warning("debate %s 终态落库撞唯一键，清理 %s 条重复发言后重写", debate_id, removed)
            if removed == 0:
                raise
            await self._write_final(debate_id, result)

    async def _dedupe_speech_rows(self, debate_id: int) -> int:
        """同一 (debate_id, seq) 只留最早一行（历史脏数据/并发写残留），返回删除条数。"""
        async with self.sessionmaker() as session:
            rows = (await session.execute(
                select(SpeechRow.id, SpeechRow.seq).where(SpeechRow.debate_id == debate_id)
                .order_by(SpeechRow.id)
            )).all()
            seen: set[int] = set()
            extra: list[int] = []
            for row_id, seq in rows:
                if seq in seen:
                    extra.append(row_id)
                else:
                    seen.add(seq)
            if extra:
                await session.execute(delete(SpeechRow).where(SpeechRow.id.in_(extra)))
                await session.commit()
            return len(extra)

    async def _write_final(self, debate_id: int, result: DebateResult) -> None:
        async with self.sessionmaker() as session:
            # 0) 谬误标注按 seq 归并——**必须在写库循环之前构建**
            #    （v1.7 曾把它放在循环之后，导致每次 finalize 必抛 UnboundLocalError：
            #     异常逃出 _job → 总线不关闭也不发终态 → 前端永久卡在"评委合议中"）
            marks_by_seq: dict[int, list] = {}
            for m in result.machine_marks:
                marks_by_seq.setdefault(m["seq"], []).append(m)

            # 1) 按 seq 去重（后写覆盖先写），并一次性取出已有行：
            #    逐行 SELECT 会触发 autoflush，把上一条待插入的行刷进同一个唯一键上，
            #    一旦 transcript 里出现重复 seq 就直接 IntegrityError（v2.2 根治）。
            by_seq: dict[int, Speech] = {}
            for speech in result.transcript:
                by_seq[speech.seq] = speech
            existing = (await session.execute(
                select(SpeechRow).where(SpeechRow.debate_id == debate_id)
            )).scalars().all()
            rows = {row.seq: row for row in existing}

            # 2) 按引擎最终 transcript 全量 upsert（质询打断会回改先前发言）
            for seq in sorted(by_seq):
                speech = by_seq[seq]
                row = rows.get(seq)
                if row is None:
                    row = SpeechRow(debate_id=debate_id, seq=seq)
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
                row.fallacies = marks_by_seq.get(seq, [])

            # 3) 评分卡（幂等：重跑/补写场景先清理旧行，避免重复累加）
            debate = await session.get(DebateRow, debate_id)
            if debate is None:  # 场次被并发删除：不再落库，交由上层按失败收尾
                raise RuntimeError(f"debate {debate_id} 不存在，无法写入终态")
            await session.execute(delete(ScorecardRow).where(ScorecardRow.debate_id == debate_id))
            # ISS-011：用户显式 stop 后，引擎若恰在终点前收尾（取消只在节点边界检查），
            # result.status 会把已写入的 stopped 无条件覆盖回 finished——"被叫停"语义被吞。
            # 落库前重读取消旗标：已请求取消的场次一律以 stopped 记终（内容字段照常写全）。
            if result.status == "finished":
                flag = self._cancel_flags.get(debate_id)
                if flag is not None and flag.get("cancelled"):
                    result = _dc_replace(result, status="stopped")
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
                # v1.9：决胜依据与智能终裁说明随场次落库（回看时不必重算）
                cfg = dict(debate.cfg_json or {})
                cfg["verdict_meta"] = {
                    "rule": result.verdict.decisive_rule,
                    "reason": result.verdict.final_reason,
                }
                debate.cfg_json = cfg
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
            # v2.9 金句摘录随终态落库（cfg_json 挂载，历史场次无此键自然为空）
            if result.golden_quotes:
                cfg = dict(debate.cfg_json or {})
                cfg["golden_quotes"] = list(result.golden_quotes)
                debate.cfg_json = cfg
            session.add(debate)
            await session.commit()

