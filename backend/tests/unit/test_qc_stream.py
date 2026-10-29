"""流式质检（v2.8.1）：边辩边检、评审阶段收割、确定性复读检测。"""
from __future__ import annotations

import threading
import time

import pytest

from debate.engine.schema import Camp, Speech
from debate.engine.tools.qc_stream import StreamingQC, repetition_ratio


def _speech(seq: int, text: str, camp: Camp = Camp.PRO, speaker: str = "pro-1",
            phase: int = 2, phase_name: str = "开篇立论") -> Speech:
    return Speech(seq=seq, phase=phase, phase_name=phase_name,
                  speaker=speaker, camp=camp, text=text)


_LONG = "我方认为技术是人的延伸，它扩展了能力边界，也重塑了交往形式，因此人类应当更主动地拥抱技术变革。" * 2
_LONG_CON = "对方把依赖等同于失控，然而依赖工具恰恰是文明演进的方式，从文字到印刷莫不如此，失控论缺乏历史依据。" * 2


def test_submit_and_harvest_collect_llm_marks(monkeypatch):
    """发言落定即分析：LLM 标注在 harvest 时被收割，注入段含阵营与发言人。"""
    from debate.engine.tools import elenchus as elenchus_module

    calls: list[int] = []

    def fake_analyze_one(text, check="fallacy", timeout=45.0, mode="inproc",
                         client=None, sensitivity=None, **kw):
        calls.append(1)
        return {"findings": [{"name": "以偏概全", "quote": "技术是人的延伸",
                              "explanation": "单一样本外推", "confidence": "中"}]}

    monkeypatch.setattr(elenchus_module, "analyze_one", fake_analyze_one)
    qc = StreamingQC(sensitivity="strict", parallel=2)
    qc.submit(_speech(1, _LONG))
    qc.submit(_speech(2, _LONG_CON, camp=Camp.CON, speaker="con-1"))
    text, marks = qc.harvest(wait=3.0)
    qc.close()
    assert len(calls) == 2
    assert len(marks) == 2
    assert {m["camp"] for m in marks} == {"pro", "con"}
    assert "【机器论证分析】" in text and "以偏概全" in text
    assert "正方·pro-1" in text and "反方·con-1" in text


def test_deterministic_repetition_mark_without_llm(monkeypatch):
    """严格性增强：本方复读由确定性规则零成本判定，不依赖 LLM、置信「高」。"""
    from debate.engine.tools import elenchus as elenchus_module

    monkeypatch.setattr(elenchus_module, "analyze_one",
                        lambda *a, **kw: {"findings": []})
    qc = StreamingQC()
    events: list[list[dict]] = []
    qc.on_marks = lambda marks: events.append(marks)
    qc.submit(_speech(1, _LONG))
    qc.submit(_speech(2, _LONG))  # 与第 1 条几乎相同 → 复读
    text, marks = qc.harvest(wait=3.0)
    qc.close()
    repeat = [m for m in marks if m["name"] == "复读论据"]
    assert len(repeat) == 1
    assert repeat[0]["seq"] == 2
    assert "高" in repeat[0]["confidence"]
    assert "第 1 条" in repeat[0]["explanation"]
    assert "复读论据" in text


def test_submit_skips_neutral_short_and_duplicates():
    qc = StreamingQC()
    neutral = _speech(1, _LONG, camp=Camp.NEUTRAL, speaker="chair", phase=1, phase_name="开场")
    short = _speech(2, "我方认为技术好。")
    qc.submit(neutral)
    qc.submit(short)
    qc.submit(_speech(3, _LONG))
    assert qc.pending == 1  # 只有第 3 条进入分析队列
    qc.close()
    assert qc.pending == 0


def test_harvest_idempotent_and_close_rejects_new(monkeypatch):
    """harvest 可多次调用（评审 join + 收尾补收割）；close 后不再接新发言。"""
    from debate.engine.tools import elenchus as elenchus_module

    monkeypatch.setattr(elenchus_module, "analyze_one",
                        lambda *a, **kw: {"findings": [{"name": "偷换概念", "quote": "x",
                                                        "explanation": "y", "confidence": "低"}]})
    qc = StreamingQC()
    qc.submit(_speech(1, _LONG))
    text1, marks1 = qc.harvest(wait=3.0)
    text2, marks2 = qc.harvest(wait=0.0)
    assert text1 == text2 and marks1 == marks2
    qc.close()
    qc.submit(_speech(2, _LONG))
    assert qc.pending == 0  # close 后 submit 被拒
    _, marks3 = qc.harvest(wait=0.0)
    assert len(marks3) == 1  # 结果不重复计


def test_slow_analysis_bounded_by_harvest_wait(monkeypatch):
    """单条分析假死 10s，harvest(wait=0.2) 也在 0.2s 量级返回——绝不拖住评委。"""
    from debate.engine.tools import elenchus as elenchus_module

    def hang(*a, **kw):
        time.sleep(10)
        return {"findings": []}

    monkeypatch.setattr(elenchus_module, "analyze_one", hang)
    qc = StreamingQC()
    started = time.monotonic()
    qc.submit(_speech(1, _LONG))
    _, marks = qc.harvest(wait=0.2)
    elapsed = time.monotonic() - started
    qc.close()
    assert marks == []
    assert elapsed < 3.0


def test_repetition_ratio_basics():
    a = "技术是人的延伸它扩展了能力边界也重塑了交往形式" * 3
    assert repetition_ratio(a, a) > 0.9
    assert repetition_ratio("完全不同的两段话内容没有重叠", "另一段话讲的是完全别的事情") == 0.0
    assert repetition_ratio("", a) == 0.0
