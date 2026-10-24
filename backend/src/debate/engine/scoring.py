"""评分解析与胜负判定（SPEC §4.7/§4.8）。确定性代码，规则归代码不归 LLM（CONSTRAINTS B4）。"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from pydantic import ValidationError

from .schema import Camp, DebateFormat, ScoreCard, Verdict

# 六维满分（PRD §5.1）
SIX_DIMS_FULL: dict[str, int] = {
    "statement": 25,
    "rebuttal_cross": 20,
    "attack": 15,
    "free": 20,
    "closing": 15,
    "conduct": 5,
}


def dims_for_format(fmt: DebateFormat) -> dict[str, int]:
    """lite 赛制无 attack 维度，其 15 分并入 rebuttal_cross（SPEC §4.7）。"""
    if fmt is DebateFormat.FULL:
        return dict(SIX_DIMS_FULL)
    dims = dict(SIX_DIMS_FULL)
    dims.pop("attack")
    dims["rebuttal_cross"] = 35
    return dims


class ScoreParseError(ValueError):
    """评委输出不合格式。"""


_FENCE = re.compile(r"```[a-zA-Z]*")
_QUOTES = ("“", "”", "「", "」", "『", "』", '"', "'", "‘", "’")


def _extract_json(raw: str) -> dict[str, Any]:
    text = _FENCE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ScoreParseError("未找到 JSON 对象")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ScoreParseError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ScoreParseError("JSON 顶层须为对象")
    return data


def _has_quote(item: str) -> bool:
    return any(q in item for q in _QUOTES)


def parse_scorecard(raw: str, *, judge: str, focus: str, fmt: DebateFormat) -> ScoreCard:
    """解析并校验一份评委输出（含 ```json 围栏容错）。"""
    data = _extract_json(raw)
    dims = dims_for_format(fmt)

    scores: dict[str, dict[str, int]] = {}
    for side in ("pro_scores", "con_scores"):
        obj = data.get(side)
        if not isinstance(obj, dict):
            raise ScoreParseError(f"缺少 {side}")
        if set(obj.keys()) != set(dims.keys()):
            raise ScoreParseError(f"{side} 维度集合不符: {sorted(map(str, obj))}")
        for key, value in obj.items():
            if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= dims[key]):
                raise ScoreParseError(f"{side}.{key} 非法: {value!r}（满分 {dims[key]}）")
        scores[side] = obj

    vote_raw = data.get("vote")
    if vote_raw not in ("pro", "con"):
        raise ScoreParseError(f"vote 非法: {vote_raw!r}")

    best = data.get("best_speaker")
    if not isinstance(best, str) or not best:
        raise ScoreParseError("缺少 best_speaker")
    best = re.sub(r"[_\s]", "-", best.strip())  # 宽容归一化：con_1 → con-1（真模型常见笔误）
    if not re.fullmatch(r"(pro|con)-[1-4]", best):
        raise ScoreParseError(f"best_speaker 非法: {best!r}")

    def side_lists(key: str) -> dict[str, list[str]]:
        obj = data.get(key)
        if not isinstance(obj, dict) or set(obj.keys()) != {"pro", "con"}:
            raise ScoreParseError(f"{key} 须含 pro/con 两侧")
        result: dict[str, list[str]] = {}
        for side in ("pro", "con"):
            items = obj[side]
            if not isinstance(items, list) or not (2 <= len(items) <= 3):
                raise ScoreParseError(f"{key}.{side} 条数须为 2~3")
            if not all(isinstance(i, str) and _has_quote(i) for i in items):
                raise ScoreParseError(f"{key}.{side} 须引用原话（引号标注）")
            result[side] = items
        return result

    highlights = side_lists("highlights")
    weaknesses = side_lists("weaknesses")

    return ScoreCard(
        judge=judge,
        focus=focus,
        pro_scores=scores["pro_scores"],
        con_scores=scores["con_scores"],
        vote=Camp(vote_raw),
        best_speaker=best,
        highlights={Camp.PRO: highlights["pro"], Camp.CON: highlights["con"]},
        weaknesses={Camp.PRO: weaknesses["pro"], Camp.CON: weaknesses["con"]},
    )


def robust_parse(
    attempts: list[str], *, judge: str, focus: str, fmt: DebateFormat
) -> ScoreCard | None:
    """逐次尝试解析；全部失败返回 None，由调用方落弃权卡（SPEC §4.7）。"""
    for raw in attempts:
        try:
            return parse_scorecard(raw, judge=judge, focus=focus, fmt=fmt)
        except (ScoreParseError, ValidationError):
            continue
    return None


def abstain_card(judge: str, focus: str, fmt: DebateFormat) -> ScoreCard:
    """弃权卡：六维 0 分、不计票、标注弃权（SPEC §4.7）。"""
    dims = dims_for_format(fmt)
    zeros = {k: 0 for k in dims}
    return ScoreCard(
        judge=judge,
        focus=focus,
        pro_scores=dict(zeros),
        con_scores=dict(zeros),
        vote=None,
        abstained=True,
    )


def judge_total(card: ScoreCard) -> int:
    return sum(card.pro_scores.values())


def team_totals(cards: list[ScoreCard], fmt: DebateFormat) -> dict[Camp, float]:
    """团体总分 = 各有效评委分项和的均值（四舍五入 1 位）；弃权不计入。"""
    valid = [c for c in cards if not c.abstained]
    if not valid:
        return {Camp.PRO: 0.0, Camp.CON: 0.0}
    pro = round(sum(judge_total(c) for c in valid) / len(valid), 1)
    con = round(sum(sum(c.con_scores.values()) for c in valid) / len(valid), 1)
    return {Camp.PRO: pro, Camp.CON: con}


def decide_winner(cards: list[ScoreCard], fmt: DebateFormat) -> Verdict:
    """三级决胜（SPEC §4.8）：票数多数 → 团体总分 → best_speaker 众数所在队。"""
    totals = team_totals(cards, fmt)
    active = [c for c in cards if not c.abstained]

    votes_counter = Counter(c.vote for c in active if c.vote is not None)
    votes = {Camp.PRO: int(votes_counter.get(Camp.PRO, 0)), Camp.CON: int(votes_counter.get(Camp.CON, 0))}

    best_counter = Counter(c.best_speaker for c in active if c.best_speaker)
    best_speaker = max(best_counter.items(), key=lambda kv: (kv[1], kv[0]))[0] if best_counter else None

    winner: Camp | None = None
    rule = "unresolved"
    if votes[Camp.PRO] != votes[Camp.CON]:
        winner = Camp.PRO if votes[Camp.PRO] > votes[Camp.CON] else Camp.CON
        rule = "majority"
    elif totals[Camp.PRO] != totals[Camp.CON]:
        winner = Camp.PRO if totals[Camp.PRO] > totals[Camp.CON] else Camp.CON
        rule = "team_total"
    elif best_counter:
        top_count = max(best_counter.values())
        tops = [s for s, n in best_counter.items() if n == top_count]
        if len(tops) == 1:
            winner = Camp.PRO if tops[0].startswith("pro") else Camp.CON
            rule = "best_speaker"

    return Verdict(
        votes=votes,
        team_totals=totals,
        winner=winner,
        best_speaker=best_speaker,
        decisive_rule=rule,
    )
