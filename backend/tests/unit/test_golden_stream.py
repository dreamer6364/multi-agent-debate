"""流式金句摘录（v2.9）：边辩边摘、原句校验、去重封顶、有界收割。"""
from __future__ import annotations

import time

import pytest

from debate.engine.schema import Camp, Speech
from debate.engine.tools import golden_stream as gs
from debate.engine.tools.golden_stream import GoldenQuoteStream, extract_quotes, _normalize


def _speech(seq: int, text: str, camp: Camp = Camp.PRO, speaker: str = "pro-1",
            phase: int = 2, phase_name: str = "开篇立论") -> Speech:
    return Speech(seq=seq, phase=phase, phase_name=phase_name,
                  speaker=speaker, camp=camp, text=text)


_LONG = "我方认为技术是人的延伸，它扩展了能力边界，也重塑了交往形式，因此人类应当更主动地拥抱技术变革。" * 2
_LONG_CON = "对方把依赖等同于失控，然而依赖工具恰恰是文明演进的方式，从文字到印刷莫不如此，失控论缺乏历史依据。" * 2

_QUOTE_A = "技术是人的延伸"
_QUOTE_B = "依赖工具恰恰是文明演进的方式"


def _fake_extract(monkeypatch, results: list[list[dict]]):
    """按调用顺序弹出预设结果（调用时导入：内部 worker 在调用点 import）。"""
    calls: list[str] = []
    it = iter(results)

    def fake(text, *, timeout=30.0, max_quotes=2):
        calls.append(text[:12])
        return next(it, [])

    monkeypatch.setattr(gs, "extract_quotes", fake)
    return calls


def test_submit_and_harvest_collect_quotes(monkeypatch):
    """发言落定即摘录：结果带 seq/camp/speaker/phase 元数据，按序累计。"""
    calls = _fake_extract(monkeypatch, [[{"quote": _QUOTE_A, "comment": "一针见血", "kind": "逻辑锋利"}],
                                        [{"quote": _QUOTE_B, "comment": "以史为证", "kind": "洞见深刻"}]])
    stream = GoldenQuoteStream(parallel=2)
    events: list[list[dict]] = []
    stream.on_quotes = lambda quotes: events.append(quotes)
    stream.submit(_speech(1, _LONG))
    stream.submit(_speech(2, _LONG_CON, camp=Camp.CON, speaker="con-1"))
    quotes = stream.harvest(wait=3.0)
    stream.close()
    assert len(calls) == 2
    assert [q["seq"] for q in quotes] == [1, 2]
    assert {q["camp"] for q in quotes} == {"pro", "con"}
    assert quotes[0]["quote"] == _QUOTE_A and quotes[0]["speaker"] == "pro-1"
    # 实时回调：两条分别上屏（不攒到终态）
    assert [len(ev) for ev in events] == [1, 1]


def test_extract_quotes_validates_verbatim(monkeypatch):
    """原句校验：LLM 返回的 quote 归一化后必须包含于发言原文，改写/编造句丢弃。"""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gs, "_chat", lambda text, timeout: "raw")
    monkeypatch.setattr(gs, "_extract_json", lambda raw: {"quotes": [
        {"quote": "技术是人的延伸", "comment": "ok", "kind": "逻辑锋利"},      # 原句 → 保留
        {"quote": "技术乃人之延伸也", "comment": "改写", "kind": "修辞出彩"},  # 非原句 → 丢弃
        {"quote": "", "comment": "空", "kind": "洞见深刻"},                     # 空 → 丢弃
        {"quote": "不存在的句子", "comment": "编造", "kind": "洞见深刻"},       # 编造 → 丢弃
        {"quote": "它扩展了能力边界，也重塑了交往形式", "comment": "ok2", "kind": "修辞出彩"},  # 原句片段 → 保留
    ]})
    quotes = extract_quotes(_LONG)
    assert [q["quote"] for q in quotes] == ["技术是人的延伸", "它扩展了能力边界，也重塑了交往形式"]


def test_extract_quotes_llm_call_and_degradation(monkeypatch):
    """extract_quotes：无 key 静默降级返回 []；LLM 坏输出/网络异常也返回 []。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert extract_quotes(_LONG) == []

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gs, "_chat", lambda text, timeout: "完全不是 JSON 的输出")
    assert extract_quotes(_LONG) == []

    def boom(text, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr(gs, "_chat", boom)
    assert extract_quotes(_LONG) == []


def test_dedup_and_total_cap(monkeypatch):
    """同句复读不重复收录；全局总数封顶（宁缺毋滥）。"""
    calls = _fake_extract(monkeypatch, [
        [{"quote": _QUOTE_A, "comment": "a", "kind": "逻辑锋利"}],
        [{"quote": _QUOTE_A + "。", "comment": "b", "kind": "修辞出彩"}],  # 同句（标点差异）→ 去重
        [{"quote": _QUOTE_B, "comment": "c", "kind": "洞见深刻"}],
        [{"quote": "从文字到印刷莫不如此", "comment": "d", "kind": "修辞出彩"}],
    ])
    stream = GoldenQuoteStream(max_total=3, parallel=1)  # 顺序执行：结果按提交序归并
    for i in range(4):
        stream.submit(_speech(i + 1, _LONG + str(i)))
    quotes = stream.harvest(wait=3.0)
    stream.close()
    assert len(calls) == 4
    assert [q["quote"] for q in quotes] == [_QUOTE_A, _QUOTE_B, "从文字到印刷莫不如此"]
    assert len(quotes) == 3  # max_total 封顶


def test_submit_skips_neutral_short_and_duplicates():
    """neutral/过短/重复提交直接跳过。"""
    stream = GoldenQuoteStream()
    stream.submit(_speech(1, _LONG, camp=Camp.NEUTRAL, speaker="chair", phase=1, phase_name="开场"))
    stream.submit(_speech(2, "我方认为技术好。"))
    stream.submit(_speech(3, _LONG))
    stream.submit(_speech(3, _LONG))  # 同 seq 重复提交
    assert stream.pending == 1
    stream.close()
    assert stream.pending == 0


def test_harvest_idempotent_and_close_rejects_new(monkeypatch):
    """harvest 可多次调用（评审 join + 收尾补收割）；close 后不再接新发言。"""
    _fake_extract(monkeypatch, [[{"quote": _QUOTE_A, "comment": "a", "kind": "逻辑锋利"}]])
    stream = GoldenQuoteStream()
    stream.submit(_speech(1, _LONG))
    q1 = stream.harvest(wait=3.0)
    q2 = stream.harvest(wait=0.0)
    assert q1 == q2
    stream.close()
    stream.submit(_speech(2, _LONG))
    assert stream.pending == 0
    q3 = stream.harvest(wait=0.0)
    assert len(q3) == 1  # 结果不重复计


def test_slow_extraction_bounded_by_harvest_wait(monkeypatch):
    """单条摘录假死 10s，harvest(wait=0.2) 也在 0.2s 量级返回——绝不拖住收尾。"""
    monkeypatch.setattr(gs, "extract_quotes", lambda *a, **kw: time.sleep(10) or [])
    stream = GoldenQuoteStream()
    started = time.monotonic()
    stream.submit(_speech(1, _LONG))
    quotes = stream.harvest(wait=0.2)
    elapsed = time.monotonic() - started
    stream.close()
    assert quotes == []
    assert elapsed < 3.0


def test_normalize_basics():
    """去重键：空白与标点全部抹平后小写比较。"""
    assert _normalize("技术，是人的延伸！") == _normalize("技术是人的延伸")
    assert _normalize(f" { _QUOTE_A } 。") == _normalize(_QUOTE_A)
    assert _normalize("") == ""


def test_track_speech_submits_golden():
    """runtime.track_speech：挂了 golden 就逐条提交（与 qc 同一入口纪律）。"""
    from types import SimpleNamespace

    from debate.engine.runtime import DebateContext
    from debate.engine.schema import DebateFormat, Topic

    submitted: list[int] = []

    class FakeGolden:
        def submit(self, speech):
            submitted.append(speech.seq)

    ctx = DebateContext(
        config=SimpleNamespace(model=SimpleNamespace(degradation_limit=3)),
        topic=Topic(topic_id="t", title="测试", pro_stance="p", con_stance="c",
                    pro_points=["p1"], con_points=["c1"]),
        agents={}, gateway=SimpleNamespace(),
        compressor=lambda speeches: {}, format=DebateFormat.FULL,
    )
    ctx.golden = FakeGolden()
    ctx.track_speech(_speech(1, _LONG))
    assert submitted == [1]


@pytest.mark.parametrize("bad", [None, {}, {"quotes": "x"}, {"quotes": [1, None]}])
def test_extract_tolerates_malformed_payload(monkeypatch, bad):
    """LLM 返回畸形结构不炸：逐项跳过，返回空列表。"""
    monkeypatch.setattr(gs, "_extract_json", lambda text: bad)
    # extract_quotes 内部对 data["quotes"] 迭代时逐 dict 校验；非 dict 直接跳过
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    assert isinstance(extract_quotes(_LONG), list)
