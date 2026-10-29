"""辩手发言 JSON 格式防线（v2.9.1）：免费档模型偶发把发言输出成评分卡 JSON，绝不上屏。"""
from __future__ import annotations

from types import SimpleNamespace

from debate.engine.llm.gateway import LLMResult
from debate.engine.nodes.phases import _ask, _looks_like_json
from debate.engine.schema import Camp, Topic

_JSON_CARD = (
    '{"pro_scores":{"statement":20,"rebuttal_cross":30,"free":15,"closing":10,"conduct":5},'
    '"con_scores":{"statement":18,"rebuttal_cross":28,"free":14,"closing":11,"conduct":4},'
    '"vote":"pro","best_speaker":"pro-1","highlights":{"pro":["a"],"con":["b"]},'
    '"weaknesses":{"pro":["c"],"con":["d"]}}'
)
_NORMAL = ("本场我方主张：真挚的感情应当建立在被爱的确定之上。"
           "第一，被爱提供情感安全的基础；第二，主动选择让关系更稳固；第三，历史与文学皆可为证。")


def _ctx(responses: list[str]):
    """最小可用 ctx：tools 关闭（phase_key=None 直接走 complete 路径）。"""
    results = [LLMResult(text=t, prompt_tokens=10, completion_tokens=10) for t in responses]

    def complete(messages, **kw):
        return results.pop(0)

    from debate.core.config import AppConfig

    config = AppConfig()
    gateway = SimpleNamespace(backend=SimpleNamespace(), complete=complete)  # backend 无 chat_with_tools
    ctx = SimpleNamespace(
        config=config, gateway=gateway, retriever=None,
        evidence={Camp.PRO: [], Camp.CON: []}, evidence_usage={},
        on_delta=None, on_event=None, degradation_streak=0, speeches=[],
        topic=Topic(topic_id="t", title="选择爱你的人还是你爱的人",
                    pro_stance="选择爱你的人", con_stance="选择你爱的人",
                    pro_points=["被爱更安稳"], con_points=["爱的人更真挚"]),
        agents={"pro-4": SimpleNamespace(camp=Camp.PRO, system_prompt="sys")},
        emit=lambda *a, **kw: None,
    )
    return ctx


def _ask_once(ctx):
    return _ask(
        ctx, speaker="pro-4", phase=8, phase_name="总结陈词",
        instruction="你正在总结陈词", cap_words=300, hard_cut=False,
        recent=[], digest_text="", phase_key=None,
    )


def test_json_lead_detected():
    assert _looks_like_json(_JSON_CARD)
    assert _looks_like_json(' {"a": 1}')
    assert _looks_like_json("[1,2,3]")
    assert not _looks_like_json(_NORMAL)
    assert not _looks_like_json("")
    assert not _looks_like_json(None)
    assert not _looks_like_json("[打断]请正面回答！")  # 合法打断标记不是 JSON


def test_json_speech_retried_and_rescued():
    """第一次输出 JSON → 带纠正指令重试 → 正常发言上屏，无格式异常注记。"""
    ctx = _ctx([_JSON_CARD, _NORMAL])
    speech = _ask_once(ctx)
    assert _JSON_CARD not in speech.text
    assert "真挚的感情" in speech.text
    assert "LLM 格式异常" not in speech.side_notes


def test_json_speech_degrades_to_placeholder_when_retry_fails():
    """重试仍输出 JSON → 降级占位，原始数据绝不进 transcript。"""
    ctx = _ctx([_JSON_CARD, _JSON_CARD])
    speech = _ask_once(ctx)
    assert "{" not in speech.text
    assert "无效内容" in speech.text
    assert "LLM 格式异常" in speech.side_notes


def test_normal_speech_untouched():
    """正常发言零干预：不重试、不改写。"""
    ctx = _ctx([_NORMAL])
    speech = _ask_once(ctx)
    assert speech.text.startswith("本场我方主张")
    assert speech.side_notes == [] or all("格式" not in n for n in speech.side_notes)
