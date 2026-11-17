"""ISS-015 #3：内容过滤 → 去证据回退（根治证据注入误拦致整场熔断，SPEC §4.9）。

场景（真实复现见 issues/evidence/_diag_a1_*.py 与 _diag_a1_failfast_verify.py）：
证据卡文本命中供应商内容黑名单 → 网关抛 ContentFiltered（ISS-015 #2 fail-fast）→
引擎去证据重建 messages 重试一次：成功则旁注"证据降级"继续，仍失败/无证据才占位降级。
"""
from __future__ import annotations

from types import SimpleNamespace

from debate.core.config import AppConfig
from debate.engine.llm.gateway import ContentFiltered, LLMResult
from debate.engine.nodes.phases import _ask
from debate.engine.rag.corpus import EvidenceCard
from debate.engine.schema import Camp, Topic

_NORMAL = (
    "本场我方主张：真挚的感情应当建立在被爱的确定之上。"
    "第一，被爱提供情感安全的基础；第二，主动选择让关系更稳固；第三，历史与文学皆可为证。"
)
FILTER_MSG = (
    "Error code: 400 - {'contentFilter': [{'level': 1, 'role': 'user'}], "
    "'error': {'code': '1301'}}"
)
_PLACEHOLDER = "[系统] 该辩手发言被内容过滤拦截"

_TRIGGER_CARD = EvidenceCard(
    card_id="c1", title="数据提取的监控逻辑",
    source="《监控资本主义时代》（肖莎娜·祖博夫）", source_type="书籍",
    text="平台经济的行为剩余价值提取。", domain="科技",
)


def _joined(messages: list[dict]) -> str:
    return "\n".join(m.get("content", "") for m in messages)


def _ctx(*, cards: list[EvidenceCard], mode: str):
    """最小可用 ctx（对齐 test_speech_guard._ctx）+ 证据卡与内容过滤故障注入。

    mode:
      - ``ok``     ：只拦含证据段的调用（去证据回退应成功）；
      - ``always`` ：全部拦截（回退也失败 / 无证据直接降级）。
    """
    calls: list[list[dict]] = []

    def complete(messages, **kw):
        calls.append(messages)
        if mode == "always" or "【证据参考】" in _joined(messages):
            raise ContentFiltered(FILTER_MSG)
        return LLMResult(text=_NORMAL, prompt_tokens=10, completion_tokens=10)

    def stream(messages, **kw):
        # 流式首打即被拦（400 先于任何 chunk，网关 ISS-015 #2 fail-fast 后同此形态）
        calls.append(messages)
        if mode == "always" or "【证据参考】" in _joined(messages):
            raise ContentFiltered(FILTER_MSG)
        raise AssertionError("本测试只覆盖流式被拦路径")
        yield  # pragma: no cover —— 使方法成为生成器

    ctx = SimpleNamespace(
        config=AppConfig(), gateway=SimpleNamespace(backend=SimpleNamespace(),
                                                    complete=complete, stream=stream),
        retriever=None, evidence={Camp.PRO: list(cards), Camp.CON: []}, evidence_usage={},
        on_delta=None, on_event=None, degradation_streak=0, speeches=[],
        topic=Topic(topic_id="t", title="选择爱你的人还是你爱的人",
                    pro_stance="选择爱你的人", con_stance="选择你爱的人",
                    pro_points=["被爱更安稳"], con_points=["爱的人更真挚"]),
        agents={"pro-4": SimpleNamespace(camp=Camp.PRO, system_prompt="sys")},
        emit=lambda *a, **kw: None,
    )

    def record_degradation():
        ctx.degradation_streak += 1  # 占位真实实现的计数语义（熔断阈值另有专测）

    ctx.record_degradation = record_degradation
    return ctx, calls


def _ask_once(ctx):
    return _ask(
        ctx, speaker="pro-4", phase=8, phase_name="总结陈词",
        instruction="你正在总结陈词", cap_words=300, hard_cut=False,
        recent=[], digest_text="", phase_key=None,
    )


def test_content_filter_falls_back_without_evidence():
    """有证据被拦 → 去证据重建重试一次成功：正常上屏、旁注"证据降级"、不计降级。"""
    ctx, calls = _ctx(cards=[_TRIGGER_CARD], mode="ok")
    speech = _ask_once(ctx)
    assert speech.text.startswith("本场我方主张")
    assert "证据降级" in speech.side_notes
    assert ctx.degradation_streak == 0  # 回退成功 ≠ 降级
    assert len(calls) == 2
    assert "【证据参考】" in _joined(calls[0])   # 首打带证据（被拦）
    assert "【证据参考】" not in _joined(calls[1])  # 回退无证据（成功）
    # 其余五段结构原样保留（回退只去证据段，不削弱任务锚定）
    assert "【辩题】" in _joined(calls[1])
    assert "【本环节任务】" in _joined(calls[1])


def test_content_filter_stream_path_also_falls_back():
    """流式路径同样走回退：stream 被拦 → complete 去证据重试成功。"""
    ctx, calls = _ctx(cards=[_TRIGGER_CARD], mode="ok")
    ctx.on_delta = lambda *a: None
    speech = _ask_once(ctx)
    assert speech.text.startswith("本场我方主张")
    assert "证据降级" in speech.side_notes
    assert ctx.degradation_streak == 0
    assert len(calls) == 2  # 流式首打（被拦）+ 回退 complete
    assert "【证据参考】" in _joined(calls[0])
    assert "【证据参考】" not in _joined(calls[1])


def test_content_filter_without_cards_degrades_immediately():
    """本就无证据可去：不重试，直接占位降级并计一次降级。"""
    ctx, calls = _ctx(cards=[], mode="always")
    speech = _ask_once(ctx)
    assert speech.text == _PLACEHOLDER
    assert "LLM 降级" in speech.side_notes
    assert "内容过滤拦截" in speech.side_notes
    assert ctx.degradation_streak == 1
    assert len(calls) == 1


def test_content_filter_fallback_also_blocked_degrades_once():
    """去证据回退仍被拦（过滤源不只证据）→ 占位降级，全程只计一次降级。"""
    ctx, calls = _ctx(cards=[_TRIGGER_CARD], mode="always")
    speech = _ask_once(ctx)
    assert speech.text == _PLACEHOLDER
    assert "内容过滤拦截" in speech.side_notes
    assert ctx.degradation_streak == 1  # 一次发言只记一笔，不因回退翻倍
    assert len(calls) == 2  # 首打 + 去证据回退各一枪


def test_no_evidence_normal_path_untouched():
    """无证据且未触发过滤的正常路径零干预（护栏：不误伤既有语义）。"""
    ctx, calls = _ctx(cards=[], mode="ok")
    speech = _ask_once(ctx)
    assert speech.text.startswith("本场我方主张")
    assert "证据降级" not in speech.side_notes
    assert ctx.degradation_streak == 0
    assert len(calls) == 1
