"""智能终裁（v1.9）：平局不再出现，最后一级由评议长裁决，任何失败都回落规则化终裁。"""
from __future__ import annotations

from types import SimpleNamespace

from debate.engine.llm.gateway import LLMExhausted
from debate.engine.nodes.officials import (
    _final_arbitration,
    _parse_arbitration,
    _rule_tiebreak,
    make_verdict_node,
)
from debate.engine.schema import Camp, DebateFormat, ScoreCard, Topic, Verdict
from debate.engine.sim.digest import Digest


def _card(judge: str, pro: int, con: int, *, vote: str | None = None) -> ScoreCard:
    """六维同分卡：总分由 pro/con 均摊，便于构造"完全打平"的评委团。"""
    def split(total: int) -> dict[str, int]:
        base, rest = divmod(total, 6)
        dims = ["statement", "rebuttal_cross", "attack", "free", "closing", "conduct"]
        return {k: base + (1 if i < rest else 0) for i, k in enumerate(dims)}

    return ScoreCard(
        judge=judge, focus="logic",
        pro_scores=split(pro), con_scores=split(con),
        vote=Camp(vote) if vote else None,
        best_speaker="pro-1" if vote == "pro" else ("con-1" if vote == "con" else None),
        highlights={Camp.PRO: ["“我方数据来自三年追踪”"], Camp.CON: ["“对方样本只覆盖城市”"]},
        weaknesses={Camp.PRO: [], Camp.CON: []},
    )


def _ctx(gateway, topic: Topic | None = None):
    topic = topic or Topic(
        topic_id="t1", title="A / B",
        pro_stance="支持 A", con_stance="支持 B",
        pro_points=["要点一"], con_points=["要点二"],
    )
    return SimpleNamespace(
        gateway=gateway,
        format=DebateFormat.FULL,
        topic=topic,
        config=SimpleNamespace(model=SimpleNamespace(temperature=SimpleNamespace(judge=0.3))),
        speeches=[],
        on_speech=None,
        emit=lambda *a, **k: None,
        check_cancel=lambda: None,
        record_degradation=lambda: None,
    )


class _JsonGateway:
    def __init__(self, text: str):
        self.text = text

    def complete(self, messages, **kwargs):  # noqa: ANN001
        return SimpleNamespace(text=self.text, prompt_tokens=1, completion_tokens=1)


class _DeadGateway:
    def complete(self, messages, **kwargs):  # noqa: ANN001
        raise LLMExhausted("超时")


def test_parse_arbitration_reads_json():
    camp, reason = _parse_arbitration('{"winner": "con", "reason": "对方始终未回应归谬"}')
    assert camp is Camp.CON and "归谬" in reason


def test_parse_arbitration_loose_fallback():
    camp, _ = _parse_arbitration("综合全场，正方在判准争夺上更稳。")
    assert camp is Camp.PRO


def test_parse_arbitration_rejects_ambiguous():
    camp, reason = _parse_arbitration("双方都很精彩，难以取舍。")
    assert camp is None and reason == ""


def test_rule_tiebreak_uses_dimension_order():
    cards = [_card("judge-logic", 78, 72), _card("judge-content", 78, 72)]
    camp, reason = _rule_tiebreak(cards, DebateFormat.FULL)
    assert camp is Camp.PRO and "statement" in reason


def test_rule_tiebreak_last_resort_never_draws():
    cards = [_card("judge-logic", 75, 75), _card("judge-content", 75, 75)]
    camp, reason = _rule_tiebreak(cards, DebateFormat.FULL)
    assert camp is Camp.PRO and "先手方" in reason


def test_final_arbitration_uses_llm_verdict():
    ctx = _ctx(_JsonGateway('{"winner":"pro","reason":"反方在核心交锋点上回避"}'))
    cards = [_card("judge-logic", 75, 75), _card("judge-content", 75, 75)]
    verdict = Verdict(votes={Camp.PRO: 1, Camp.CON: 1}, team_totals={Camp.PRO: 75.0, Camp.CON: 75.0},
                      winner=None, decisive_rule="unresolved")
    camp, reason = _final_arbitration(ctx, {"digest": Digest(), "scorecards": cards}, verdict)
    assert camp is Camp.PRO and "回避" in reason


def test_final_arbitration_falls_back_when_llm_dead():
    ctx = _ctx(_DeadGateway())
    cards = [_card("judge-logic", 78, 72)]
    verdict = Verdict(votes={Camp.PRO: 1, Camp.CON: 1}, team_totals={Camp.PRO: 78.0, Camp.CON: 72.0},
                      winner=None, decisive_rule="unresolved")
    camp, reason = _final_arbitration(ctx, {"digest": Digest(), "scorecards": cards}, verdict)
    assert camp is Camp.PRO and "规则化终裁" in reason


def test_verdict_node_never_returns_draw():
    """评委投票与总分完全持平 → 节点仍产出胜方，决胜依据标记为 final_judge。"""
    cards = [_card("judge-logic", 75, 75, vote="pro"), _card("judge-content", 75, 75, vote="con")]
    ctx = _ctx(_DeadGateway())
    node = make_verdict_node(ctx)
    result = node({"transcript": [], "digest": Digest(), "scorecards": cards})
    verdict = result["verdict"]
    assert verdict.winner is not None
    assert verdict.decisive_rule == "final_judge"
    assert verdict.final_reason
    assert "本场胜方" in result["transcript"][0].text
