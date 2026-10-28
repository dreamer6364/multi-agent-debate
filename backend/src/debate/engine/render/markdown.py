"""Markdown 导出（SPEC §8 / CONSTRAINTS F5）：transcript.md 与 scorecard.md。"""
from __future__ import annotations

from datetime import datetime

from ..procedure import PHASE_NUMBERS
from ..schema import CAMP_CN, Camp, DebateFormat, ScoreCard, Speech, Verdict
from ..sim.timer import NOTE_TIME_UP

_JUDGE_CN = {"judge-logic": "逻辑评委", "judge-content": "内容评委", "judge-expression": "表达评委"}
_CN_NUM = {"1": "一", "2": "二", "3": "三", "4": "四"}


def role_display(role_id: str) -> str:
    if role_id.startswith("pro-"):
        return f"正方·{_CN_NUM[role_id.split('-')[1]]}辩"
    if role_id.startswith("con-"):
        return f"反方·{_CN_NUM[role_id.split('-')[1]]}辩"
    return _JUDGE_CN.get(role_id, {"chair": "主席", "timekeeper": "计时员"}.get(role_id, role_id))


def _budget_suffix(phase: int, config) -> str:
    phase_key = next((k for k, v in PHASE_NUMBERS.items() if v == phase), None)
    if phase_key in ("judging", "verdict", "closing_ceremony"):
        return ""  # 无固定时长环节（评委/宣判/致辞）不标时长
    minutes = getattr(config.procedure.phases, phase_key, None) if phase_key else None
    if minutes is None:  # 未知环节不要因取值失败而中断整篇导出
        return ""
    if phase_key == "free_debate":
        return f"，每队 {minutes:g} 分钟"
    if phase_key in ("cross_examination", "attack"):
        return f"，每组 {minutes:g} 分钟"
    return f"，时间 {minutes:g} 分钟"


def _fmt_num(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:g}" if isinstance(value, float) else str(value)


def _winner_line(verdict: Verdict | None) -> str:
    if verdict is None or verdict.winner is None:
        return "未分胜负"
    return f"{CAMP_CN[verdict.winner]}获胜"


def render_transcript_md(topic, fmt: DebateFormat, result, config) -> str:
    usage = result.usage
    tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    lines = [
        f"# 辩论实录：{topic.title}",
        "",
        f"- 赛制：{'完整版' if fmt is DebateFormat.FULL else '精简版'}",
        f"- 日期：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 结果：{_winner_line(result.verdict)}",
        f"- LLM 用量：{usage.get('calls', 0)} 次调用 / {tokens} tokens",
        "",
    ]
    current_phase: int | None = None
    for speech in result.transcript:
        if speech.phase != current_phase:
            current_phase = speech.phase
            lines.append(f"## 【环节 {speech.phase}｜{speech.phase_name}】{_budget_suffix(speech.phase, config)}")
            lines.append("")
        lines.append(f"**{role_display(speech.speaker)}**：{speech.text}")
        for note in speech.side_notes:
            lines.append(f"*（{note}）*")
        if NOTE_TIME_UP in speech.side_notes:
            lines.append("（被计时截断）")
        lines.append("")
    return "\n".join(lines)


def render_scorecard_md(topic, fmt: DebateFormat, result) -> str:
    verdict = result.verdict
    lines = [f"# 评分表：{topic.title}", ""]
    if not result.scorecards:
        lines.append("（本场均无有效评分——比赛未完成或评委全部弃权。）")
        return "\n".join(lines)

    lines += [
        "| 评委 | 正方 | 反方 | 投票 | 亮点（正方 / 反方） | 不足（正方 / 反方） |",
        "|---|---|---|---|---|---|",
    ]
    for card in result.scorecards:
        if card.abstained:
            lines.append(f"| {card.judge}（弃权） | 0 | 0 | 弃权 | 输出异常，评分作废 | 输出异常，评分作废 |")
            continue
        vote_cn = CAMP_CN[card.vote] if card.vote else "弃权"
        highlights = " / ".join(
            "；".join(card.highlights.get(camp, [])) or "（无）" for camp in (Camp.PRO, Camp.CON)
        )
        weaknesses = " / ".join(
            "；".join(card.weaknesses.get(camp, [])) or "（无）" for camp in (Camp.PRO, Camp.CON)
        )
        pro_total = sum(card.pro_scores.values())
        con_total = sum(card.con_scores.values())
        lines.append(
            f"| {_JUDGE_CN.get(card.judge, card.judge)} | {_fmt_num(pro_total)} | {_fmt_num(con_total)} "
            f"| {vote_cn} | {highlights} | {weaknesses} |"
        )
    totals = verdict.team_totals if verdict else {Camp.PRO: 0.0, Camp.CON: 0.0}
    votes = verdict.votes if verdict else {Camp.PRO: 0, Camp.CON: 0}
    best = verdict.best_speaker if verdict else None
    lines += [
        f"| **结果** | 团体总分 {_fmt_num(totals[Camp.PRO])} | 团体总分 {_fmt_num(totals[Camp.CON])} "
        f"| {votes[Camp.PRO]} : {votes[Camp.CON]} | **{_winner_line(verdict)}** | 最佳辩手：{best or '无'} |",
        "",
    ]
    if verdict and verdict.decisive_rule and verdict.decisive_rule != "majority":
        rule_cn = {"team_total": "平票，按团体总分决胜", "best_speaker": "平票且总分相同，按最佳辩手归属决胜",
                   "final_judge": "投票与总分均平，由评议长智能终裁",
                   "unresolved": "决胜规则用尽，判为平局"}
        lines.append(f"- 决胜说明：{rule_cn.get(verdict.decisive_rule, verdict.decisive_rule)}。")
        if verdict.final_reason:
            lines.append(f"- 终裁理由：{verdict.final_reason}")
        lines.append("")
    usage = result.usage
    tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    lines += [f"- LLM 用量：{usage.get('calls', 0)} 次调用 / {tokens} tokens。", ""]
    return "\n".join(lines)
