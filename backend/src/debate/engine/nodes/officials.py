"""工作人员节点：主席开场（phase 1）与宣判（phase 10）。

报幕/宣判由代码模板执行（AGENT_CONSTRAINTS §2：chair 的 LLM 只产开场白与致谢两段）；
宣判数据来自评分引擎，逐字播报，不得改判。
"""
from __future__ import annotations

from ..agents.context import build_debater_messages
from ..llm.gateway import LLMExhausted
from ..procedure import PHASE_NAMES, PHASE_NUMBERS, minutes_for
from ..runtime import DebateContext
from ..schema import CAMP_CN, Camp, Speech
from ..sim.conduct import scan, strip_forbidden_format
from ..sim.timer import TurnClock, truncate_to_sentence, words_for
from ..scoring import decide_winner
from .phases import _emit


def make_opening_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["opening"], PHASE_NAMES["opening"]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base = state["transcript"]
        budget = words_for(minutes_for("opening", ctx.config), ctx.config.procedure.speed_cpm)
        ctx.emit("phase_start", phase=phase, name=phase_name, actors=["chair"], budget_words=budget)
        spec = ctx.agents["chair"]
        messages = build_debater_messages(
            spec=spec, topic=ctx.topic, digest_text="",
            recent=base,
            instruction=(
                "你是主席：致开场白。槽位顺序：欢迎 → 介绍辩题 → 介绍双方队伍（正方支持前半句，反方支持后半句）"
                "→ 介绍评委团 → 规则提示（含计时与礼仪）→ 宣布比赛开始。只说流程，不评价内容。"
                f"（本次发言预算约 {budget} 字）"
            ),
            recent_k=0,
            max_input_tokens=ctx.config.context.max_input_tokens,
        )
        try:
            result = ctx.gateway.complete(
                messages, tier="chair",
                temperature=ctx.config.model.temperature.chair,
                max_tokens=budget * 2,
            )
            ctx.degradation_streak = 0
        except LLMExhausted:
            ctx.record_degradation()
            result = None
        if result is None:
            text, notes = "[系统] 主席开场降级为流程播报。", ["LLM 降级"]
        else:
            text, _ = strip_forbidden_format(result.text.strip())
            notes = scan(text, ctx.config.conduct.abuse_keywords)
        text, _ = truncate_to_sentence(text, budget)
        clock = TurnClock(budget, ctx.config.procedure.speed_cpm)
        clock_notes = clock.consume(len(text))
        notes.extend(clock_notes)
        for note in clock_notes:
            if note == "计时员：还剩 30 秒":
                ctx.emit("timer", type="warning", speaker="chair")
            elif note == "计时员：时间到":
                ctx.emit("timer", type="time_up", speaker="chair")
        speech = Speech(
            seq=len(base) + 1, phase=phase, phase_name=phase_name,
            speaker="chair", camp=Camp.NEUTRAL, text=text, side_notes=notes,
            word_count=len(text),
            tokens=(result.prompt_tokens + result.completion_tokens) if result else 0,
        )
        new = [speech]
        _emit(ctx, new, speech)
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "digest": state["digest"]}

    return node


def make_verdict_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["verdict"], PHASE_NAMES["verdict"]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base = state["transcript"]
        verdict = decide_winner(state["scorecards"], ctx.format)
        ctx.emit("phase_start", phase=phase, name=phase_name, actors=["chair"], budget_words=0)
        votes, totals = verdict.votes, verdict.team_totals
        rule_cn = {
            "majority": "评委投票多数",
            "team_total": "团体总分",
            "best_speaker": "最佳辩手归属",
            "unresolved": "决胜规则用尽",
        }[verdict.decisive_rule]
        if verdict.winner is None:
            result_line = "本场平局，未分出胜方。"
        else:
            result_line = f"本场胜方：{CAMP_CN[verdict.winner]}。"
        text = (
            f"现在宣布本场比赛结果：评委投票正方 {votes[Camp.PRO]} 票，反方 {votes[Camp.CON]} 票；"
            f"团体总分正方 {totals[Camp.PRO]} 分，反方 {totals[Camp.CON]} 分（决胜依据：{rule_cn}）。"
            f"{result_line}最佳辩手：{verdict.best_speaker or '无'}。"
            "感谢双方辩手的精彩表现，感谢评委团的辛勤工作，本场比赛到此结束。"
        )
        speech = Speech(
            seq=len(base) + 1, phase=phase, phase_name=phase_name,
            speaker="chair", camp=Camp.NEUTRAL, text=text, word_count=len(text),
        )
        new = [speech]
        _emit(ctx, new, speech)
        ctx.emit("verdict",
                 votes={"pro": votes[Camp.PRO], "con": votes[Camp.CON]},
                 team_totals={"pro": totals[Camp.PRO], "con": totals[Camp.CON]},
                 winner=verdict.winner.value if verdict.winner else None,
                 best_speaker=verdict.best_speaker,
                 scorecards=[{
                     "judge": c.judge, "focus": c.focus,
                     "vote": c.vote.value if c.vote else None,
                     "best_speaker": c.best_speaker, "abstained": c.abstained,
                     "pro_total": sum(c.pro_scores.values()),
                     "con_total": sum(c.con_scores.values()),
                 } for c in state["scorecards"]])
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "verdict": verdict}

    return node
