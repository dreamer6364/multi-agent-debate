"""xlsx 评分导出（PRD v2.1 剩余项）：openpyxl 生成评分表工作簿。

Sheet1「评分与结果」：对局元信息 + 评委总分行 + 亮点/不足明细；
Sheet2「六维明细」：各评委六个维度的正反分项。
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..schema import CAMP_CN, Camp, DebateFormat
from .markdown import _budget_suffix, role_display

_BOLD = Font(bold=True)
_HEADER_FILL = PatternFill("solid", fgColor="E6E9F5")
_DIMS: list[tuple[str, int]] = [
    ("立论", 25), ("驳论质询", 20), ("攻辩", 15), ("自由辩", 20), ("总结", 15), ("风度", 5),
]

RULE_CN = {
    "majority": "评委投票多数",
    "team_total": "团体总分",
    "best_speaker": "最佳辩手归属",
    "unresolved": "决胜规则用尽",
}


def _fmt_num(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_scorecard_xlsx(topic, fmt: DebateFormat, result, config) -> bytes:
    verdict = result.verdict
    wb = Workbook()

    # ---------- Sheet1 评分与结果 ----------
    ws = wb.active
    ws.title = "评分与结果"
    ws.append([f"辩论评分表：{topic.title}"])
    ws["A1"].font = Font(bold=True, size=14)
    usage = result.usage
    winner_line = "未分胜负"
    if verdict is not None and verdict.winner is not None:
        winner_line = f"{CAMP_CN[verdict.winner]}获胜"
    ws.append(["赛制", "完整版" if fmt is DebateFormat.FULL else "精简版",
               "日期", datetime.now().astimezone().isoformat(timespec="seconds")])
    ws.append(["结果", winner_line,
               "LLM 用量", f"{usage.get('calls', 0)} 次调用 / "
               f"{usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)} tokens"])
    ws.append([])

    ws.append(["评委", "正方", "反方", "投票", "最佳辩手"])
    for cell in ws[ws.max_row]:
        cell.font = _BOLD
        cell.fill = _HEADER_FILL
    for card in result.scorecards:
        if card.abstained:
            ws.append([f"{role_display(card.judge)}（弃权）", 0, 0, "弃权", "—"])
            continue
        ws.append([
            role_display(card.judge),
            sum(card.pro_scores.values()),
            sum(card.con_scores.values()),
            CAMP_CN[card.vote] if card.vote else "弃权",
            role_display(card.best_speaker) if card.best_speaker else "无",
        ])
    if verdict is not None:
        rule_cn = RULE_CN.get(verdict.decisive_rule, verdict.decisive_rule)
        ws.append([
            "结果",
            f"团体总分 {_fmt_num(verdict.team_totals[Camp.PRO])}",
            f"团体总分 {_fmt_num(verdict.team_totals[Camp.CON])}",
            f"{verdict.votes[Camp.PRO]} : {verdict.votes[Camp.CON]}",
            f"胜方：{'未分' if verdict.winner is None else CAMP_CN[verdict.winner]}｜最佳辩手：{best_of(verdict)}",
        ])
        for cell in ws[ws.max_row]:
            cell.font = _BOLD
        ws.append([f"决胜依据：{rule_cn}"])
    ws.append([])

    ws.append(["亮点与不足（引用原话）"])
    ws.cell(row=ws.max_row, column=1).font = _BOLD
    ws.append(["评委", "类型", "阵营", "内容"])
    for cell in ws[ws.max_row]:
        cell.font = _BOLD
        cell.fill = _HEADER_FILL
    for card in result.scorecards:
        if card.abstained:
            continue
        for kind, source in (("亮点", card.highlights), ("不足", card.weaknesses)):
            for camp in (Camp.PRO, Camp.CON):
                for item in source.get(camp, []):
                    ws.append([role_display(card.judge), kind, CAMP_CN[camp], item])

    for col, width in zip("ABCD", (22, 14, 14, 110)):
        ws.column_dimensions[col].width = width

    # ---------- Sheet2 六维明细 ----------
    ws2 = wb.create_sheet("六维明细")
    ws2.append(["维度", *[f"{c.judge}·正方" for c in result.scorecards],
                *[f"{c.judge}·反方" for c in result.scorecards]])
    for cell in ws2[1]:
        cell.font = _BOLD
        cell.fill = _HEADER_FILL
    for dim, _ in _DIMS:
        row: list[object] = [dim]
        for card in result.scorecards:
            row.append(card.pro_scores.get(dim, 0))
        for card in result.scorecards:
            row.append(card.con_scores.get(dim, 0))
        ws2.append(row)
    for col in "ABCDEFGHIJKL":
        ws2.column_dimensions[col].width = 16

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def best_of(verdict) -> str:
    return role_display(verdict.best_speaker) if verdict.best_speaker else "无"
