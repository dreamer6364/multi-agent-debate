"""评分与胜负单测（TEST_PLAN UT-SCO-01~05，SPEC §4.7/§4.8）。"""
import json

import pytest

from debate.engine.schema import Camp, DebateFormat, ScoreCard
from debate.engine.scoring import (
    ScoreParseError,
    abstain_card,
    decide_winner,
    dims_for_format,
    parse_scorecard,
    robust_parse,
    team_totals,
)

GOOD = {
    "pro_scores": {"statement": 20, "rebuttal_cross": 15, "attack": 10, "free": 15, "closing": 10, "conduct": 5},
    "con_scores": {"statement": 18, "rebuttal_cross": 14, "attack": 12, "free": 14, "closing": 11, "conduct": 4},
    "vote": "pro",
    "best_speaker": "pro-3",
    "highlights": {"pro": ["“开宗明义”定义清晰", "“判准”一以贯之"], "con": ["“安全阀”比喻贴切", "“驳论”句式整齐"]},
    "weaknesses": {"pro": ["“数据”未给来源", "“自由辩”接招偏慢"], "con": ["“定义”退让过多", "“小结”未回扣判准"]},
}

D_PRO = {"statement": 20, "rebuttal_cross": 15, "attack": 10, "free": 15, "closing": 10, "conduct": 5}  # 75
D_CON = {"statement": 18, "rebuttal_cross": 14, "attack": 12, "free": 14, "closing": 11, "conduct": 4}  # 73


def raw(obj) -> str:
    return "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


def card(vote: Camp, best: str = "pro-3", judge: str = "j1", pro: dict | None = None, con: dict | None = None) -> ScoreCard:
    return ScoreCard(
        judge=judge, focus="logic",
        pro_scores=dict(pro or D_PRO), con_scores=dict(con or D_CON),
        vote=vote, best_speaker=best,
    )


def test_parse_valid_with_fences():
    parsed = parse_scorecard(raw(GOOD), judge="judge-logic", focus="logic", fmt=DebateFormat.FULL)
    assert parsed.vote is Camp.PRO
    assert parsed.best_speaker == "pro-3"
    assert parsed.pro_scores["attack"] == 10
    assert parsed.highlights[Camp.PRO] == GOOD["highlights"]["pro"]


def test_parse_missing_dim():
    bad = json.loads(json.dumps(GOOD))
    bad["pro_scores"].pop("attack")
    with pytest.raises(ScoreParseError):
        parse_scorecard(raw(bad), judge="j", focus="logic", fmt=DebateFormat.FULL)


def test_parse_score_out_of_range():
    bad = json.loads(json.dumps(GOOD))
    bad["pro_scores"]["statement"] = 30
    with pytest.raises(ScoreParseError):
        parse_scorecard(raw(bad), judge="j", focus="logic", fmt=DebateFormat.FULL)


def test_parse_highlight_count_must_be_2_to_3():
    bad = json.loads(json.dumps(GOOD))
    bad["highlights"]["pro"] = ["只有一条“点评”"]
    with pytest.raises(ScoreParseError):
        parse_scorecard(raw(bad), judge="j", focus="logic", fmt=DebateFormat.FULL)


def test_parse_highlight_must_quote():
    bad = json.loads(json.dumps(GOOD))
    bad["highlights"]["pro"] = ["没有引号的点评一", "没有引号的点评二"]
    with pytest.raises(ScoreParseError):
        parse_scorecard(raw(bad), judge="j", focus="logic", fmt=DebateFormat.FULL)


def test_robust_parse_recovers_or_abstains():
    assert robust_parse(["not json", raw(GOOD)], judge="j", focus="logic", fmt=DebateFormat.FULL) is not None
    assert robust_parse(["x", "y", "z"], judge="j", focus="logic", fmt=DebateFormat.FULL) is None


def test_abstain_card_zeroed_and_not_counted():
    abstain = abstain_card("judge-logic", "logic", DebateFormat.FULL)
    assert abstain.abstained and abstain.vote is None
    assert sum(abstain.pro_scores.values()) == 0
    totals = team_totals([parse_scorecard(raw(GOOD), judge="j", focus="logic", fmt=DebateFormat.FULL), abstain],
                         DebateFormat.FULL)
    assert totals[Camp.PRO] == float(sum(GOOD["pro_scores"].values()))  # 弃权不计入均值


def test_team_totals_mean_rounded():
    totals = team_totals(
        [card(Camp.PRO, judge="j1"), card(Camp.PRO, judge="j2", pro={"statement": 19, "rebuttal_cross": 15, "attack": 10, "free": 15, "closing": 10, "conduct": 5})],
        DebateFormat.FULL,
    )
    assert totals[Camp.PRO] == 74.5


def test_majority_decides():
    verdict = decide_winner([card(Camp.PRO), card(Camp.PRO, judge="j2"), card(Camp.CON, judge="j3")], DebateFormat.FULL)
    assert verdict.winner is Camp.PRO and verdict.decisive_rule == "majority"


def test_tie_resolved_by_team_total():
    verdict = decide_winner([card(Camp.PRO), card(Camp.CON, judge="j2")], DebateFormat.FULL)
    assert verdict.winner is Camp.PRO and verdict.decisive_rule == "team_total"  # 75 vs 73


def test_tie_resolved_by_best_speaker():
    equal = dict(D_PRO)
    verdict = decide_winner(
        [card(Camp.PRO, pro=equal, con=dict(equal)), card(Camp.CON, judge="j2", pro=equal, con=dict(equal))],
        DebateFormat.FULL,
    )
    assert verdict.winner is Camp.PRO and verdict.decisive_rule == "best_speaker"


def test_unresolved_when_all_tied():
    equal = dict(D_PRO)
    verdict = decide_winner(
        [
            card(Camp.PRO, pro=equal, con=dict(equal), best="pro-3"),
            card(Camp.CON, judge="j2", pro=equal, con=dict(equal), best="con-3"),
        ],
        DebateFormat.FULL,
    )
    assert verdict.winner is None and verdict.decisive_rule == "unresolved"


def test_best_speaker_underscore_normalized():
    bad = json.loads(json.dumps(GOOD))
    bad["best_speaker"] = "con_1"  # 真模型常见笔误：下划线
    parsed = parse_scorecard(raw(bad), judge="j", focus="logic", fmt=DebateFormat.FULL)
    assert parsed.best_speaker == "con-1"
    invalid = json.loads(json.dumps(GOOD))
    invalid["best_speaker"] = "主持人"
    with pytest.raises(ScoreParseError):
        parse_scorecard(raw(invalid), judge="j", focus="logic", fmt=DebateFormat.FULL)


def test_lite_dims_merge_attack_into_rebuttal():
    assert dims_for_format(DebateFormat.LITE) == {
        "statement": 25, "rebuttal_cross": 35, "free": 20, "closing": 15, "conduct": 5,
    }
