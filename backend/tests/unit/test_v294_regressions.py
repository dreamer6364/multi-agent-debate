"""v2.9.4 回归：实时推送修复（BE-P1-1）+ 重连标注快照（BE-P2-2）。"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from debate.engine.schema import Camp, Speech
from debate.engine.tools.qc_stream import StreamingQC


def _speech(seq: int, text: str, camp: Camp = Camp.PRO) -> Speech:
    return Speech(seq=seq, phase=2, phase_name="开篇立论", speaker="pro-1", camp=camp, text=text)


_LONG = "我方认为技术是人的延伸，它扩展了能力边界，也重塑了交往形式，因此人类应当更主动地拥抱技术变革。" * 2


def test_track_speech_polls_qc_and_golden_after_submit():
    """BE-P1-1：track_speech 在 submit 后必须立即 poll（harvest(0)）。

    此前 drain/emit 只发生在评审阶段 harvest 与终态——直播全程的
    fallacy / golden_quote SSE 事件被积压到赛后才一次性补发，实时特性失效。
    """
    from debate.engine.runtime import DebateContext

    calls: list[object] = []
    qc = SimpleNamespace(
        submit=lambda s: calls.append("qc-submit"),
        harvest=lambda w: calls.append(("qc-harvest", w)),
    )
    golden = SimpleNamespace(
        submit=lambda s: calls.append("g-submit"),
        harvest=lambda w: calls.append(("g-harvest", w)),
    )
    ctx = SimpleNamespace(qc=qc, golden=golden, on_event=None)  # on_event=None 跳过泳道
    DebateContext.track_speech(ctx, _speech(1, _LONG))
    assert ("qc-harvest", 0) in calls
    assert ("g-harvest", 0) in calls
    assert calls.index(("qc-harvest", 0)) > calls.index("qc-submit")


def test_harvest_zero_poll_emits_marks_without_full_wait(monkeypatch):
    """BE-P1-1：harvest(0) 即时收割已完成条目并触发 on_marks（无需等评审阶段）。"""
    from debate.engine.tools import elenchus as elenchus_module

    monkeypatch.setattr(elenchus_module, "analyze_one",
                        lambda *a, **kw: {"findings": [{"name": "以偏概全", "quote": "技术是人的延伸",
                                                        "explanation": "单一样本外推", "confidence": "中"}]})
    qc = StreamingQC()
    events: list[list[dict]] = []
    qc.on_marks = lambda marks: events.append(marks)
    qc.submit(_speech(1, _LONG))
    # 模拟 track_speech 的即时 poll：分析是瞬时的，最多几轮内应推出口径
    for _ in range(50):
        qc.harvest(0)
        if events:
            break
        time.sleep(0.02)
    qc.close()
    assert events and any(m["name"] == "以偏概全" for m in events[0])


def test_event_bus_accumulates_fallacy_marks_for_replay():
    """BE-P2-2：EventBus 快照 fallacy_marks——重连补发与金句对称。"""
    from debate.services.event_bus import EventBus

    bus = EventBus(1)
    bus.publish({"event": "fallacy", "data": {"marks": [{"seq": 1, "name": "复读论据"}]}})
    bus.publish({"event": "fallacy", "data": {"marks": [{"seq": 2, "name": "偷换概念"}]}})
    bus.publish({"event": "speech", "data": {"seq": 2}})  # speech 不进快照
    assert [m["name"] for m in bus.fallacy_marks] == ["复读论据", "偷换概念"]


def test_shutdown_bounded_wait_pending_task():
    """BE-P2-3：shutdown 有界等待——挂死的引擎任务不阻塞停机超过 timeout 量级。"""
    from debate.services.debate_service import DebateService

    async def scenario():
        service = DebateService.__new__(DebateService)  # 跳过 __init__，只测 shutdown 纪律
        service._cancel_flags = {}
        service._tasks = {}
        flag = {"cancelled": False}
        service._cancel_flags[1] = flag

        async def hung_job():
            await asyncio.sleep(120)  # 模拟卡在长 LLM 调用，不响应取消

        task = asyncio.create_task(hung_job())
        service._tasks[1] = task
        started = time.monotonic()
        await asyncio.wait_for(service.shutdown(), timeout=20.0)
        elapsed = time.monotonic() - started
        assert flag["cancelled"] is True
        assert elapsed < 18.0  # 15s timeout + 余量，而非 120s
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
