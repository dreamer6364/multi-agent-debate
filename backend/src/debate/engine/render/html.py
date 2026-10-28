"""HTML 精排报告（SPEC §8）：Jinja2 单文件、内联 CSS、零外部请求、零 JS（CONSTRAINTS F6）。"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..schema import CAMP_CN, Camp, DebateFormat
from .markdown import NOTE_TIME_UP, _budget_suffix, role_display

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

DIMS: list[tuple[str, int]] = [
    ("立论", 25), ("驳论质询", 20), ("攻辩", 15), ("自由辩", 20), ("总结", 15), ("风度", 5),
]

RULE_CN = {
    "majority": "评委投票多数",
    "team_total": "团体总分",
    "best_speaker": "最佳辩手归属",
    "final_judge": "评议长智能终裁",
    "unresolved": "决胜规则用尽",
}


def _role_class(role: str) -> str:
    if role.startswith("pro-"):
        return "pro"
    if role.startswith("con-"):
        return "con"
    if role == "timekeeper":
        return "timekeeper"
    if role.startswith("judge-"):
        return "judge"
    return "chair"


def _fmt_num(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _radar(scorecards) -> dict:
    """六维雷达：评分卡未存分维明细，按满分比例把团体均分摊成正六边形展示口径。"""
    valid = [c for c in scorecards if not c.abstained]
    total_max = sum(maximum for _, maximum in DIMS)
    pro_avg = round(sum(sum(c.pro_scores.values()) for c in valid) / len(valid), 1) if valid else 0.0
    con_avg = round(sum(sum(c.con_scores.values()) for c in valid) / len(valid), 1) if valid else 0.0

    def polygon(fraction: float) -> str:
        fraction = max(0.0, min(1.0, fraction / total_max))
        points = []
        for index in range(len(DIMS)):
            angle = math.pi * 2 * index / len(DIMS) - math.pi / 2
            radius = fraction * 80
            points.append(f"{110 + radius * math.cos(angle):.1f},{100 + radius * math.sin(angle):.1f}")
        return " ".join(points)

    rings = [polygon(fraction * total_max) for fraction in (0.25, 0.5, 0.75, 1.0)]
    axes = []
    for index, (label, _) in enumerate(DIMS):
        angle = math.pi * 2 * index / len(DIMS) - math.pi / 2
        axes.append({
            "x": f"{110 + 80 * math.cos(angle):.1f}", "y": f"{100 + 80 * math.sin(angle):.1f}",
            "lx": f"{110 + 98 * math.cos(angle):.1f}", "ly": f"{100 + 98 * math.sin(angle):.1f}",
            "label": label,
        })
    return {"rings": rings, "axes": axes,
            "pro": polygon(pro_avg), "con": polygon(con_avg),
            "pro_avg": _fmt_num(pro_avg), "con_avg": _fmt_num(con_avg)}


def render_report_html(topic, fmt: DebateFormat, result, config) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    sections: list[dict] = []
    current: dict | None = None
    current_phase: int | None = None
    for speech in result.transcript:
        if current is None or current_phase != speech.phase:
            current_phase = speech.phase
            current = {
                "anchor": f"phase-{speech.phase}",
                "label": f"环节 {speech.phase}｜{speech.phase_name}",
                "budget": _budget_suffix(speech.phase, config),
                "speeches": [],
            }
            sections.append(current)
        current["speeches"].append({
            "cls": _role_class(speech.speaker),
            "speaker": role_display(speech.speaker),
            "text": speech.text,
            "notes": list(speech.side_notes),
            "truncated": NOTE_TIME_UP in speech.side_notes,
        })

    verdict = result.verdict
    banner_text = banner_class = ""
    best = role_display(verdict.best_speaker) if verdict and verdict.best_speaker else "无"
    if verdict is not None:
        rule_cn = RULE_CN.get(verdict.decisive_rule, verdict.decisive_rule)
        if verdict.winner is None:
            banner_class = "tie"
            banner_text = f"本场平局，未分出胜方（决胜依据：{rule_cn}）；最佳辩手：{best}"
        else:
            banner_class = "pro" if verdict.winner is Camp.PRO else "con"
            banner_text = (f"本场胜方：{CAMP_CN[verdict.winner]}；"
                           f"投票 {verdict.votes[Camp.PRO]} : {verdict.votes[Camp.CON]}（{rule_cn}）；最佳辩手：{best}")

    scorecards = result.scorecards
    scorecard_rows = [{
        "judge": role_display(c.judge) + ("（弃权）" if c.abstained else ""),
        "pro_total": _fmt_num(sum(c.pro_scores.values())),
        "con_total": _fmt_num(sum(c.con_scores.values())),
        "vote": CAMP_CN[c.vote] if c.vote else "弃权",
        "best": role_display(c.best_speaker) if c.best_speaker else "无",
    } for c in scorecards]

    usage = result.usage
    winner_line = "未分胜负"
    if verdict is not None and verdict.winner is not None:
        winner_line = f"{CAMP_CN[verdict.winner]}获胜"

    return template.render(
        title=topic.title,
        fmt_cn="完整版" if fmt is DebateFormat.FULL else "精简版",
        date=datetime.now().astimezone().isoformat(timespec="seconds"),
        winner_line=winner_line,
        usage_calls=usage.get("calls", 0),
        usage_tokens=usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
        banner_text=banner_text,
        banner_class=banner_class,
        sections=sections,
        scorecards=scorecard_rows,
        totals_pro=_fmt_num(verdict.team_totals[Camp.PRO]) if verdict else "0",
        totals_con=_fmt_num(verdict.team_totals[Camp.CON]) if verdict else "0",
        votes_pro=verdict.votes[Camp.PRO] if verdict else 0,
        votes_con=verdict.votes[Camp.CON] if verdict else 0,
        best=best,
        rule_cn=RULE_CN.get(verdict.decisive_rule, "") if verdict else "",
        radar=_radar(scorecards) if scorecards else None,
    )
