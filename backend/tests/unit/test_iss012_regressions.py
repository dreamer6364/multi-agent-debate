"""ISS-012（v2.9.5）低危项修复回归：原子 usage、生命周期回收、事件 id、流式重试、异常收割、0 时长环节。"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from debate.core.config import AppConfig
from debate.engine.llm.gateway import Gateway, LLMResult
from debate.engine.nodes.phases import _ask, make_monologue_node
from debate.engine.schema import Camp, DebateFormat, Topic


# ---- BE-1：usage 原子累加 ----

def test_usage_add_is_locked_and_counts_all_concurrent_calls():
    gw = Gateway(backend=None)
    entered = []

    class SpyLock:
        def __enter__(self):
            entered.append(1)
            return self

        def __exit__(self, *args):
            return False

    gw._usage_lock = SpyLock()
    gw._add_usage(prompt_tokens=3, completion_tokens=4)
    assert entered and gw.usage == {"calls": 1, "prompt_tokens": 3, "completion_tokens": 4}

    gw._usage_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(500):
            gw._add_usage(prompt_tokens=1, completion_tokens=2)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert gw.usage["calls"] == 4001
    assert gw.usage["prompt_tokens"] == 4003
    assert gw.usage["completion_tokens"] == 8004  # 初始 4 + 4000 次 × 2


# ---- BE-2 / BE-3：总线与配额锁回收、非 speech id 独立高位段 ----

def test_event_bus_ids_live_in_separate_space_from_speech_seq():
    from debate.services.event_bus import NON_SPEECH_ID_BASE, EventBus

    bus = EventBus(1)
    bus.publish({"event": "speech", "id": 7, "data": {"seq": 7}})
    bus.publish({"event": "phase_start", "data": {"phase": 1}})
    assert bus.allocate_id() >= NON_SPEECH_ID_BASE
    ids = [bus.allocate_id(), bus.allocate_id()]
    assert ids[1] == ids[0] + 1
    assert all(i > 10_000 for i in ids)  # 单场发言数远低于此，两个空间不会重叠


def test_quota_guard_prunes_only_idle_entries_and_keeps_mutual_exclusion():
    from debate.services.debate_service import DebateService, _QuotaEntry

    service = DebateService.__new__(DebateService)
    service._quota_locks = {}
    service._buses = {}
    service._tasks = {}
    service._cancel_flags = {}

    async def scenario():
        async with service.quota_guard(1):
            assert 1 in service._quota_locks
            assert service._quota_locks[1].users == 1
        assert service._quota_locks[1].users == 0  # 退出临界区后在途计数归零

        # 空闲且过期 → 回收；未过期 / 在途 → 保留
        service._quota_locks[1].last_used = time.monotonic() - 400
        stale = _QuotaEntry()
        stale.last_used = time.monotonic() - 400
        service._quota_locks[2] = stale
        in_flight = _QuotaEntry()
        in_flight.users = 1
        in_flight.last_used = time.monotonic() - 400
        service._quota_locks[3] = in_flight
        service._prune_quota_locks()
        assert 1 not in service._quota_locks and 2 not in service._quota_locks
        assert 3 in service._quota_locks

        # 两个并发创建仍串行化（回收不能让同一用户出现两把锁）
        order = []
        async def job(name):
            async with service.quota_guard(9):
                order.append(f"{name}-in")
                await asyncio.sleep(0.01)
                order.append(f"{name}-out")
        await asyncio.gather(job("a"), job("b"))
        assert order in (["a-in", "a-out", "b-in", "b-out"],
                         ["b-in", "b-out", "a-in", "a-out"])

    asyncio.run(scenario())


def test_bus_is_reclaimed_after_terminal_replay_window(monkeypatch):
    from debate.services import debate_service as svc_module
    from debate.services.debate_service import DebateService
    from debate.services.event_bus import EventBus

    monkeypatch.setattr(svc_module, "BUS_REPLAY_TTL_SECONDS", 0.0)
    service = DebateService.__new__(DebateService)
    service._buses = {1: EventBus(1)}

    async def scenario():
        bus = service._buses[1]
        bus.close()
        service._schedule_bus_reclaim(1, bus)
        assert service.get_bus(1) is bus  # 窗口内仍可补发
        await asyncio.sleep(0.02)
        assert service.get_bus(1) is None  # 窗口过后回收，不再常驻内存

    asyncio.run(scenario())


# ---- BE-4：流式 JSON 重试先发 attempt 重置信号 ----

_JSON_CARD = '{"pro_scores":{"statement":20},"vote":"pro"}'
_NORMAL = "本场我方主张：真挚的感情应当建立在被爱的确定之上，因此选择被爱更安稳。"


def _streaming_ctx(responses: list[str], deltas: list[tuple]):
    cfg = AppConfig()
    results = list(responses)

    def complete(messages, **kw):
        return LLMResult(text=results.pop(0), prompt_tokens=10, completion_tokens=10)

    def stream(messages, **kw):
        on_attempt = kw.get("on_attempt")
        if on_attempt is not None:
            on_attempt(0)
        text = results.pop(0)
        for i in range(0, len(text), 8):
            yield text[i:i + 8]

    gateway = SimpleNamespace(backend=SimpleNamespace(), complete=complete, stream=stream)
    return SimpleNamespace(
        config=cfg, gateway=gateway, retriever=None,
        evidence={Camp.PRO: [], Camp.CON: []}, evidence_usage={},
        on_delta=lambda seq, delta, attempt, speaker, camp: deltas.append(
            (seq, delta, attempt, speaker, camp)),
        on_event=None, degradation_streak=0, speeches=[],
        topic=Topic(topic_id="t", title="选择爱你的人还是你爱的人",
                    pro_stance="选择爱你的人", con_stance="选择你爱的人",
                    pro_points=["被爱更安稳"], con_points=["爱的人更真挚"]),
        agents={"pro-4": SimpleNamespace(camp=Camp.PRO, system_prompt="sys")},
        emit=lambda *a, **kw: None,
    )


def test_streamed_json_retry_sends_attempt_reset_before_reusing_slot():
    deltas: list[tuple] = []
    ctx = _streaming_ctx([_JSON_CARD, _NORMAL], deltas)
    speech = _ask(
        ctx, speaker="pro-4", phase=8, phase_name="总结陈词",
        instruction="你正在总结陈词", cap_words=300, hard_cut=False,
        recent=[], digest_text="", phase_key=None,
    )
    assert "真挚的感情" in speech.text
    resets = [d for d in deltas if d[1] == ""]
    assert len(resets) == 2  # 流开始一次 + JSON 重试一次
    assert resets[-1][2] == 1  # attempt 递增，前端据此处清空脏 JSON 累积
    assert resets[-1][0] == speech.seq
    assert any("{" in d[1] for d in deltas[:-1])  # 脏 JSON 确实先推给了前端
    assert deltas[-1] == resets[-1]  # 重置是最后一条增量，之后是终稿 speech


def test_non_streamed_json_retry_does_not_emit_reset():
    """没有推过增量的路径不发重置信号（避免前端凭空出现空打字卡）。"""
    deltas: list[tuple] = []
    ctx = _streaming_ctx([_JSON_CARD, _NORMAL], deltas)
    ctx.on_delta = None
    _ask(ctx, speaker="pro-4", phase=8, phase_name="总结陈词",
         instruction="你正在总结陈词", cap_words=300, hard_cut=False,
         recent=[], digest_text="", phase_key=None)
    assert deltas == []


# ---- BE-5：runner 未预期异常路径仍收割金句 ----

def test_unexpected_graph_exception_still_harvests_golden_quotes(monkeypatch):
    from debate.engine import runner as runner_module
    from debate.engine.tools import golden_stream as golden_module

    calls: list[float] = []

    class FakeGolden:
        def __init__(self, **kwargs):
            pass

        def harvest(self, wait):
            calls.append(wait)
            return []

        def close(self):
            calls.append(-1.0)

    cfg = AppConfig().model_dump()
    cfg["rag"]["enabled"] = False
    cfg["web_search"]["enabled"] = False
    cfg["tools"]["elenchus"]["enabled"] = False
    cfg["tools"]["golden"]["enabled"] = True
    config = AppConfig.model_validate(cfg)

    class BoomGraph:
        def invoke(self, state):
            raise RuntimeError("graph blew up")

    monkeypatch.setattr(runner_module, "build_graph", lambda ctx, fmt: BoomGraph())
    monkeypatch.setattr(golden_module, "GoldenQuoteStream", FakeGolden)

    with pytest.raises(RuntimeError, match="graph blew up"):
        runner_module.run_debate(
            topic=Topic(topic_id="t", title="辩题", pro_stance="正", con_stance="反",
                        pro_points=["a"], con_points=["b"]),
            fmt=DebateFormat.LITE, config=config,
            gateway=SimpleNamespace(usage={"calls": 0}),
            compressor=lambda speeches: {}, agents={},
        )
    assert 1.0 in calls  # 异常路径收割
    assert -1.0 in calls  # finally 仍关闭


# ---- BE-6：0 时长环节跳过而非熔断整场 ----

def test_zero_minute_monologue_phase_is_skipped_not_aborted():
    from debate.engine.graph.state import initial_state

    cfg = AppConfig()
    cfg.procedure.phases.statement = 0.0
    events: list[tuple] = []
    ctx = SimpleNamespace(
        config=cfg, check_cancel=lambda: None,
        emit=lambda kind, **data: events.append((kind, data)),
    )
    state = initial_state()
    out = make_monologue_node("statement", ctx)(state)
    assert out["transcript"] == state["transcript"]  # 无发言、无 LLM 调用
    assert [kind for kind, _ in events] == ["phase_start", "phase_end"]  # 环节戳仍成对
    assert events[0][1]["budget_words"] == 0
