"""评委节点（phase 9）：3 位评委并行点评（ARCH D5），结构化输出 + 弃权降级（SPEC §4.7）。"""
from __future__ import annotations

import asyncio

from ..agents.context import build_judge_messages
from ..llm.gateway import LLMExhausted
from ..procedure import PHASE_NAMES, PHASE_NUMBERS
from ..runtime import DebateContext
from ..schema import CAMP_CN, Camp, ScoreCard, Speech
from ..scoring import ScoreParseError, abstain_card, dims_for_format, parse_scorecard
from ..sim.digest import render_digest
from .phases import _emit

JUDGE_FOCUS = {"judge-logic": "logic", "judge-content": "content", "judge-expression": "expression"}


def _judge_instruction(fmt) -> str:
    dims = dims_for_format(fmt)
    dims_text = "、".join(f"{k} {v} 分" for k, v in dims.items())
    return (
        "请输出你的评审结果，只输出一个 JSON 对象，字段："
        f'pro_scores 与 con_scores（六维，满分：{dims_text}）；'
        'vote（"pro" 或 "con"）；best_speaker（全场最佳辩手 role_id）；'
        "highlights 与 weaknesses（各含 pro/con 两侧，每侧 2~3 条，每条必须用引号引用具体发言原话）。"
        "你的侧重见角色文件；打分与点评结论必须自洽。"
    )


def _checklist(transcript: list[Speech]) -> str:
    phases = sorted({s.phase for s in transcript})
    return f"全场比赛共 {len(transcript)} 条发言，历经环节 {phases}。"


def _judge_summary_text(card: ScoreCard) -> str:
    if card.abstained:
        return "本评委输出异常，按规则弃权，评分作废。"
    vote_cn = CAMP_CN[card.vote] if card.vote else "弃权"
    def join(items: dict[Camp, list[str]], camp: Camp) -> str:
        return "；".join(items.get(camp, [])) or "（无）"
    return (
        f"评审完成。投票：{vote_cn}；最佳辩手：{card.best_speaker}。"
        f"正方亮点：{join(card.highlights, Camp.PRO)}。反方亮点：{join(card.highlights, Camp.CON)}。"
        f"正方不足：{join(card.weaknesses, Camp.PRO)}。反方不足：{join(card.weaknesses, Camp.CON)}。"
    )


def make_judging_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["judging"], PHASE_NAMES["judging"]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base = state["transcript"]
        judge_ids = [rid for rid in ctx.agents if rid.startswith("judge-")][: ctx.config.judges.count]
        ctx.emit("phase_start", phase=phase, name=phase_name, actors=judge_ids, budget_words=0)
        ctx.emit("judging", judges=judge_ids)
        digest_text = render_digest(state["digest"])
        checklist = _checklist(base)

        async def one(judge_id: str) -> ScoreCard:
            spec = ctx.agents[judge_id]
            messages = build_judge_messages(
                spec=spec, topic=ctx.topic, digest_text=digest_text,
                excerpts=state["digest"].excerpts, checklist=checklist,
                instruction=_judge_instruction(ctx.format),
            )
            attempts: list[str] = []
            card = None
            last_error = ""
            for _ in range(3):  # 首次 + 纠错重试 2 次（带失败原因反馈，SPEC §4.7）
                try:
                    result = ctx.gateway.complete(
                        messages, tier="judge",
                        temperature=ctx.config.model.temperature.judge,
                        max_tokens=1200,
                    )
                except LLMExhausted:
                    break
                attempts.append(result.text)
                try:
                    card = parse_scorecard(result.text, judge=judge_id,
                                           focus=JUDGE_FOCUS[judge_id], fmt=ctx.format)
                    break
                except ScoreParseError as exc:
                    last_error = str(exc)
                    # 纠错反馈：失败原因与原输出回传，引导模型修正
                    messages = messages + [
                        {"role": "assistant", "content": result.text},
                        {"role": "user", "content": (
                            f"你的输出不合格式：{last_error}。请重新只输出一个 JSON 对象，"
                            "并修正以下常见问题：1) highlights/weaknesses 每侧 2~3 条，"
                            "且每条用引号引用具体发言原话；2) best_speaker 使用连字符格式"
                            "（如 pro-3 / con-2）；3) 不要输出 JSON 以外的任何文字。"
                        )},
                    ]
            return card if card is not None else abstain_card(judge_id, JUDGE_FOCUS[judge_id], ctx.format)

        async def run_all() -> list[ScoreCard]:
            return list(await asyncio.gather(*(one(j) for j in judge_ids)))

        cards = asyncio.run(run_all())

        new: list[Speech] = []
        seq = len(base) + 1
        for card in cards:
            speech = Speech(
                seq=seq, phase=phase, phase_name=phase_name,
                speaker=card.judge, camp=Camp.NEUTRAL,
                text=_judge_summary_text(card), word_count=0,
            )
            seq += 1
            _emit(ctx, new, speech)
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "scorecards": cards}

    return node
