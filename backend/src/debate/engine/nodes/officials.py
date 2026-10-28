"""工作人员节点：主席开场（phase 1）与宣判（phase 10）。

报幕/宣判由代码模板执行（AGENT_CONSTRAINTS §2：chair 的 LLM 只产开场白与致谢两段）；
宣判数据来自评分引擎，逐字播报，不得改判。
"""
from __future__ import annotations

import concurrent.futures
import json
import re

from ..agents.context import build_chair_messages
from ..llm.gateway import LLMExhausted
from ..procedure import PHASE_NAMES, PHASE_NUMBERS, minutes_for
from ..runtime import DebateContext
from ..schema import CAMP_CN, Camp, Speech, Topic, Verdict
from ..scoring import decide_winner, dims_for_format
from ..sim.conduct import scan, strip_forbidden_format
from ..sim.digest import render_digest
from ..sim.timer import TurnClock, truncate_to_sentence, words_for
from .phases import _emit, next_seq


# ---- 主席发言守卫（v1.9.2）：辩题锚定 + 中立性 --------------------------
# 病根：主席此前拿不到辩题（NEUTRAL 的立场/要点为空，消息里没有【辩题】），
# 引题演讲必然自说自话。现在既补注入，也对生成结果做一致性校验与兜底。
_PUNCT = re.compile(r"[\s，。、；：！？,.;:!?\"'“”‘’（）()《》【】〈〉<>—…·/／|~～]+")
# 明确站边/预判语汇（保守取词，避免误伤辩题自带表述）
_BIAS_MARKERS = ("我方", "显然", "毫无疑问", "毋庸置疑", "更有道理", "站不住脚",
                 "荒谬", "可笑", "稳赢", "必输", "必败", "毫无价值")


def _grams(text: str, n: int = 2) -> set[str]:
    body = _PUNCT.sub("", text or "")
    return {body[i:i + n] for i in range(max(0, len(body) - n + 1))}


# 贴题阈值：辩题 2-gram 覆盖率低于该值视为"换了个话题"
MIN_TOPIC_RATIO = 0.18


def topic_drift_ratio(text: str, topic: Topic) -> float:
    """返回值越大越贴题：演讲命中辩题 2-gram 的比例（0~1）。

    不用分词：中文改写的容错靠二元组覆盖率，阈值取低值即可拦住"换了个话题"的演讲。
    """
    ref = _grams(topic.title)
    if not ref:
        return 1.0
    return len(ref & _grams(text)) / len(ref)


def neutrality_issue(text: str) -> str:
    """中立性检查：显式站边/预判词 → 原因；只提一方而不提另一方 → 原因；否则空串。"""
    for word in _BIAS_MARKERS:
        if word in text:
            return f"出现倾向性表述「{word}」"
    if ("正方" in text) != ("反方" in text):
        return "只提及一方，未对双方作平衡交代"
    return ""


def _keynote_fallback(topic: Topic) -> str:
    """确定性兜底引题：不含任何立场判断，必然贴题。仅在两次生成均不合格时使用。"""
    pro = topic.pro_stance or "支持辩题前半句"
    con = topic.con_stance or "支持辩题后半句"
    return (
        f"今晚的辩题是：{topic.title}。"
        f"正方站在「{pro}」这一边，反方站在「{con}」这一边。"
        "真正值得盯住的不是谁嗓门更大，而是双方如何定义同一个关键词、如何为各自的主张找证据。"
        f"一方要说明{pro}在现实中如何落地，另一方要说明{con}为什么更稳妥。"
        "接下来请留意三个看点：谁的定义更稳、谁的论据经得起追问、以及在对方最强的一击上谁给出了正面回应。"
        "双方辩手已经就位。"
    )


def _opening_fallback(topic: Topic) -> str:
    return (
        f"欢迎来到本场辩论赛。今晚的辩题是：{topic.title}。"
        f"正方主张{topic.pro_stance or '辩题前半句'}，反方主张{topic.con_stance or '辩题后半句'}。"
        "本场评委将从逻辑、内容与表达三个维度评分。各环节限时发言，"
        "剩余三十秒有计时提醒，请双方遵守发言秩序与礼仪。现在，比赛开始。"
    )


# ---------------------------------------------------------------------


def make_opening_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["opening"], PHASE_NAMES["opening"]
    # 开篇引题演讲（赛前导览，不计入赛制时长）：面向观众，中立不站边
    keynote_budget = 420

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base = state["transcript"]
        budget = words_for(minutes_for("opening", ctx.config), ctx.config.procedure.speed_cpm)
        ctx.emit("phase_start", phase=phase, name=phase_name, actors=["chair"], budget_words=0)
        produced: list[Speech] = []

        # ① 开篇引题演讲（v1.9.1 前移）：中立主席把辩题讲得值得看——在比赛正式开始之前完成
        _chair_speak(
            ctx, base, produced, phase=phase, phase_name=phase_name,
            instruction=(
                "你是本场主席。此刻辩论尚未正式开始，你先面向现场与直播间观众做「开篇引题演讲」，"
                f"把这场辩题讲得值得看。本场辩题是「{ctx.topic.title}」。槽位顺序："
                "①第一句必须原样点出本场辩题（「今晚的辩题是：……」），不得改写或替换为相近话题；"
                "②用一个具体场景、新闻或尖锐追问切入这道辩题背后的现实张力；"
                "③点明双方真正的分歧焦点（不是复述立场，而是找出争议的核心变量）；"
                "④各用一句话预告正方与反方的核心主张（只能依据给定的【正方立场】【反方立场】表述，"
                "不得虚构具体论点编号、数据或论证结构，不评价优劣，不排序）；"
                "⑤给出本场两到三个看点或悬念，告诉观众该盯哪里；"
                "⑥结尾示意导览结束、比赛即将开始。"
                "硬性约束：演讲必须严格围绕上述辩题展开，举例只是举例、必须回扣辩题，"
                "禁止滑向其它议题（如泛谈时代、教育、人性）；严格中立，不表态支持任何一方、"
                "不预判胜负、不评价辩手水平；"
                "语言有画面感、节奏干净，避免「众所周知」「让我们拭目以待」一类套话；不分点罗列，成段讲述。"
            ),
            cap=keynote_budget, use_clock=False, fallback=_keynote_fallback(ctx.topic),
        )
        # ② 流程开场白：报幕收尾并宣布比赛开始（此前引题误在开赛之后，已对调）
        _chair_speak(
            ctx, base, produced, phase=phase, phase_name=phase_name,
            instruction=(
                "你是主席：现在致开场白并宣布比赛开始。槽位顺序：欢迎 → 原样介绍辩题"
                f"（「{ctx.topic.title}」）→ 介绍双方队伍（正方主张{ctx.topic.pro_stance or '前半句'}，"
                f"反方主张{ctx.topic.con_stance or '后半句'}）→ 介绍评委团 → 规则提示（含计时与礼仪）→ 宣布比赛开始。"
                "只说流程，不评价内容。"
            ),
            cap=budget, use_clock=True, clock_words=budget, fallback=_opening_fallback(ctx.topic),
        )
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + produced, "digest": state["digest"]}

    return node


def _chair_speak(
    ctx: DebateContext,
    base: list[Speech],
    produced: list[Speech],
    *,
    phase: int,
    phase_name: str,
    instruction: str,
    cap: int,
    use_clock: bool = False,
    clock_words: int = 0,
    fallback: str,
) -> Speech:
    """主席单段发言：LLM 生成 → 辩题锚定/中立校验（不合格重写一次）→ 兜底模板。

    v1.9.2：主席消息改由 build_chair_messages 注入【辩题】+双方立场；
    生成结果必须贴题且中立，两次不合格即改用确定性模板，杜绝跑题与站边。
    v2.2：抽出为模块级函数，开篇/报幕/结束致辞三处共用同一道守卫。
    """
    spec = ctx.agents["chair"]
    result = None
    text = ""
    notes: list[str] = []
    issue = ""
    for attempt in range(2):
        retry_hint = "" if attempt == 0 else (
            f"\n【重写要求】上一条发言不合格：{issue}。"
            f"重写时必须原样点出本场辩题「{ctx.topic.title}」，"
            f"并同时交代正方（{ctx.topic.pro_stance or '辩题前半句'}）与"
            f"反方（{ctx.topic.con_stance or '辩题后半句'}）的主张；全程中立，不评价任何一方。"
        )
        messages = build_chair_messages(
            spec=spec, topic=ctx.topic,
            instruction=f"{instruction}{retry_hint}（本次发言预算约 {cap} 字）",
        )
        try:
            result = ctx.gateway.complete(
                messages, tier="chair",
                temperature=ctx.config.model.temperature.chair,
                max_tokens=cap * 2,
            )
            ctx.degradation_streak = 0
        except LLMExhausted:
            ctx.record_degradation()
            result = None
        if result is None:
            text, notes = fallback, ["LLM 降级"]
            break
        candidate, _ = strip_forbidden_format(result.text.strip())
        candidate, _ = truncate_to_sentence(candidate, cap)
        issue = ""
        if topic_drift_ratio(candidate, ctx.topic) < MIN_TOPIC_RATIO:
            issue = "内容偏离本场辩题（与辩题关键词几乎不重合）"
        else:
            issue = neutrality_issue(candidate)
        if not issue:
            text = candidate
            notes = scan(text, ctx.config.conduct.abuse_keywords)
            break
        if attempt == 1:  # 两次都不合格 → 确定性兜底，绝不把跑题内容播出去
            text = fallback
            notes = scan(text, ctx.config.conduct.abuse_keywords) + ["主席发言已按辩题模板兜底"]
    if use_clock:
        clock = TurnClock(clock_words or cap, ctx.config.procedure.speed_cpm)
        clock_notes = clock.consume(len(text))
        notes.extend(clock_notes)
        for note in clock_notes:
            if note == "计时员：还剩 30 秒":
                ctx.emit("timer", type="warning", speaker="chair")
            elif note == "计时员：时间到":
                ctx.emit("timer", type="time_up", speaker="chair")
    speech = Speech(
        seq=next_seq(list(base) + list(produced)), phase=phase, phase_name=phase_name,
        speaker="chair", camp=Camp.NEUTRAL, text=text, side_notes=notes,
        word_count=len(text),
        tokens=(result.prompt_tokens + result.completion_tokens) if result else 0,
    )
    # 注意：_emit 内部已 new.append(speech)，此处不可再手动 append（v1.9.2 修重复入实录）
    _emit(ctx, produced, speech)
    return speech


_ARBITER_SYSTEM = (
    "你是本场辩论赛的评议长（首席评委）。当评委投票与团体总分完全打平时，由你做最终裁决。"
    "你只依据场上已发生的论证做判断，必须二选一，不得平局、不得弃权、不得以『双方都很优秀』收场。"
)

_ARBITER_TASK = (
    "【终裁任务】双方在评委票数与团体总分上完全持平，按规则无法分出胜负。请做整体判断："
    "哪一方的论证整体更站得住——定义是否清晰一致、论据是否真正支撑主张、对对方核心攻击是否实质回应、"
    "价值落点是否成立，以及在关键交锋点上谁守住了阵地。"
    "只输出一个 JSON 对象：{\"winner\": \"pro\" 或 \"con\", \"reason\": \"不超过 120 字，指出决定胜负的那一处关键\"}。"
    "除 JSON 外不要输出任何文字。"
)


def _scorecard_brief(cards) -> str:
    lines: list[str] = []
    for c in cards:
        if c.abstained:
            continue
        vote_cn = CAMP_CN[c.vote] if c.vote else "弃权"
        lines.append(
            f"- {c.judge}（侧重 {c.focus}）：正方 {sum(c.pro_scores.values())} / "
            f"反方 {sum(c.con_scores.values())}，投票 {vote_cn}，最佳辩手 {c.best_speaker or '无'}"
        )
        for camp, label in ((Camp.PRO, "正方"), (Camp.CON, "反方")):
            hi = "；".join(c.highlights.get(camp, []))
            lo = "；".join(c.weaknesses.get(camp, []))
            if hi or lo:
                lines.append(f"  · {label}亮点：{hi or '（无）'}｜{label}不足：{lo or '（无）'}")
    return "\n".join(lines) or "（无有效评分卡）"


def _parse_arbitration(text: str) -> tuple[Camp | None, str]:
    """解析终裁输出：优先 JSON，其次"只提到一方"的宽松兜底；失败返回 (None, "")。"""
    block = re.search(r"\{[\s\S]*\}", text or "")
    if block:
        try:
            data = json.loads(block.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            raw = str(data.get("winner", "")).strip().lower()
            reason = str(data.get("reason", "")).strip()
            if raw.startswith("pro") or raw in {"正方", "positive"}:
                return Camp.PRO, reason
            if raw.startswith("con") or raw in {"反方", "negative"}:
                return Camp.CON, reason
    # 宽松兜底：全文只出现一方称谓时按其判定
    has_pro, has_con = "正方" in text, "反方" in text
    if has_pro != has_con:
        camp = Camp.PRO if has_pro else Camp.CON
        return camp, (text or "").strip()[:120]
    return None, ""


def _rule_tiebreak(cards, fmt) -> tuple[Camp, str]:
    """规则化终裁（LLM 不可用时的确定兜底）：按维度权重逐项比较 → 最佳辩手票数 → 先手方。"""
    valid = [c for c in cards if not c.abstained]
    if not valid:
        return Camp.PRO, "无有效评分卡，按先手方（正方）判定"
    dims = dims_for_format(fmt)
    for key in ("statement", "rebuttal_cross", "attack", "free", "closing", "conduct"):
        if key not in dims:
            continue
        pro = sum(c.pro_scores.get(key, 0) for c in valid) / len(valid)
        con = sum(c.con_scores.get(key, 0) for c in valid) / len(valid)
        if pro != con:
            camp = Camp.PRO if pro > con else Camp.CON
            return camp, f"规则化终裁：{key} 维度均值正方 {pro:.1f} / 反方 {con:.1f}，{CAMP_CN[camp]}占优"
    pro_best = sum(1 for c in valid if (c.best_speaker or "").startswith("pro"))
    con_best = sum(1 for c in valid if (c.best_speaker or "").startswith("con"))
    if pro_best != con_best:
        camp = Camp.PRO if pro_best > con_best else Camp.CON
        return camp, f"规则化终裁：最佳辩手票数正方 {pro_best} / 反方 {con_best}"
    return Camp.PRO, "规则化终裁：双方全部维度持平，按先手方（正方）判定"


def _final_arbitration(ctx: DebateContext, state: dict, verdict: Verdict) -> tuple[Camp, str]:
    """评议长智能终裁：LLM 综合裁决；超时/任何失败都回落规则化终裁，保证不卡宣判、一定有胜方。"""
    try:
        messages = [
            {"role": "system", "content": _ARBITER_SYSTEM},
            {"role": "user", "content": "\n\n".join([
                f"【辩题】{ctx.topic.title}",
                f"【正方立场】{ctx.topic.pro_stance}\n【正方破题要点】\n"
                + "\n".join(f"- {p}" for p in ctx.topic.pro_points),
                f"【反方立场】{ctx.topic.con_stance}\n【反方破题要点】\n"
                + "\n".join(f"- {p}" for p in ctx.topic.con_points),
                f"【战局摘要】\n{render_digest(state['digest'])}",
                f"【评委打分（已打平）】\n{_scorecard_brief(state['scorecards'])}",
                _ARBITER_TASK,
            ])},
        ]
        # v1.9.1：终裁调用限时（含 429 长退避场景），到点即走规则化兜底，绝不让宣判无限等待
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="chair-arbiter")
        try:
            fut = pool.submit(
                ctx.gateway.complete, messages, tier="judge",
                temperature=min(0.3, ctx.config.model.temperature.judge),
                max_tokens=400,
            )
            result = fut.result(timeout=90.0)
        except concurrent.futures.TimeoutError:
            ctx.record_degradation()
            return _rule_tiebreak(state["scorecards"], ctx.format)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        camp, reason = _parse_arbitration(result.text)
        if camp is not None:
            return camp, reason or "评议长综合全场论证质量判定"
    except LLMExhausted:
        ctx.record_degradation()
    except Exception:  # noqa: BLE001 —— 终裁不可阻塞宣判
        pass
    return _rule_tiebreak(state["scorecards"], ctx.format)


def _closing_fallback(topic: Topic, winner_cn: str) -> str:
    """确定性兜底致辞：贴题、中立、只致谢不评判。"""
    return (
        f"本场关于「{topic.title}」的讨论到这里就结束了。"
        f"感谢正方坚持{topic.pro_stance or '自己的立场'}，也感谢反方坚持{topic.con_stance or '自己的立场'}，"
        "双方在定义、论据与回应上都给出了值得回味的段落。"
        "一场辩论的价值不只在于分出胜负，更在于它把一个问题问得更清楚："
        "该守住什么、可以让渡什么、代价由谁来承担，这些问题会被带出赛场。"
        f"本场胜方是{winner_cn}，但两支队伍都值得掌声。感谢评委团的评议，感谢各位观众，我们下一场再见。"
    )


def make_closing_ceremony_node(ctx: DebateContext):
    """主席结束致辞（phase 11）：宣判之后、全场结束之前的收束段。

    v2.2 新增：此前宣判完就直接 END，观众听不到"收场"。致辞只做致谢与回望，
    不改判、不评价评委打分，且必须贴题、保持中立（沿用主席发言守卫）。
    """
    phase, phase_name = PHASE_NUMBERS["closing_ceremony"], PHASE_NAMES["closing_ceremony"]
    closing_budget = 320

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base = state["transcript"]
        verdict: Verdict | None = state.get("verdict")
        winner_cn = CAMP_CN[verdict.winner] if verdict and verdict.winner else "正方"
        produced: list[Speech] = []
        ctx.emit("phase_start", phase=phase, name=phase_name, actors=["chair"], budget_words=0)
        _chair_speak(
            ctx, base, produced, phase=phase, phase_name=phase_name,
            instruction=(
                f"你是本场主席，比赛结果已经宣布，现在做「结束致辞」收束全场。本场辩题是「{ctx.topic.title}」，"
                f"胜方为{winner_cn}。槽位顺序："
                "①回望本场辩题留下的真问题（不是复述赛果，而是把观众带出赛场时该带走的那一问）；"
                "②各用一句话肯定正方与反方在场上最有价值的那次交锋或论证（不比较优劣、不给分数）；"
                "③致谢评委团与观众，宣布本场比赛结束。"
                "硬性约束：结果已定，不得改判、不得质疑或解释评委打分；严格中立，不评价谁更强；"
                "语言干净、有收束感，避免套话与排比堆砌，成段讲述。"
            ),
            cap=closing_budget, fallback=_closing_fallback(ctx.topic, winner_cn),
        )
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + produced}

    return node


def make_verdict_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["verdict"], PHASE_NAMES["verdict"]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base = state["transcript"]
        verdict = decide_winner(state["scorecards"], ctx.format)
        # v1.9：三级决胜仍平局时，由评议长做智能终裁——赛果不再出现平局
        if verdict.winner is None:
            winner, reason = _final_arbitration(ctx, state, verdict)
            verdict = verdict.model_copy(update={
                "winner": winner, "decisive_rule": "final_judge", "final_reason": reason,
            })
        ctx.emit("phase_start", phase=phase, name=phase_name, actors=["chair"], budget_words=0)
        votes, totals = verdict.votes, verdict.team_totals
        rule_cn = {
            "majority": "评委投票多数",
            "team_total": "团体总分",
            "best_speaker": "最佳辩手归属",
            "final_judge": "评议长智能终裁",
            "unresolved": "决胜规则用尽",
        }[verdict.decisive_rule]
        if verdict.winner is None:
            result_line = "本场平局，未分出胜方。"
        else:
            result_line = f"本场胜方：{CAMP_CN[verdict.winner]}。"
        final_note = f"（终裁说明：{verdict.final_reason}）" if verdict.final_reason else ""
        text = (
            f"现在宣布本场比赛结果：评委投票正方 {votes[Camp.PRO]} 票，反方 {votes[Camp.CON]} 票；"
            f"团体总分正方 {totals[Camp.PRO]} 分，反方 {totals[Camp.CON]} 分（决胜依据：{rule_cn}）。"
            f"{result_line}{final_note}最佳辩手：{verdict.best_speaker or '无'}。"
            "感谢双方辩手的精彩表现，感谢评委团的辛勤工作，本场比赛到此结束。"
        )
        speech = Speech(
            seq=next_seq(base), phase=phase, phase_name=phase_name,
            speaker="chair", camp=Camp.NEUTRAL, text=text, word_count=len(text),
        )
        # 注意：_emit 内部已 new.append(speech)——这里必须传空列表，
        # 否则宣判发言会在实录/落库队列里出现两次（v2.2 曾因此撞 uq_debate_seq）
        new: list[Speech] = []
        _emit(ctx, new, speech)
        ctx.emit("verdict",
                 votes={"pro": votes[Camp.PRO], "con": votes[Camp.CON]},
                 team_totals={"pro": totals[Camp.PRO], "con": totals[Camp.CON]},
                 winner=verdict.winner.value if verdict.winner else None,
                 best_speaker=verdict.best_speaker,
                 decisive_rule=verdict.decisive_rule,
                 final_reason=verdict.final_reason,
                 scorecards=[{
                     "judge": c.judge, "focus": c.focus,
                     "vote": c.vote.value if c.vote else None,
                     "best_speaker": c.best_speaker, "abstained": c.abstained,
                     "pro_total": sum(c.pro_scores.values()),
                     "con_total": sum(c.con_scores.values()),
                     "pro_scores": c.pro_scores, "con_scores": c.con_scores,
                     "highlights": {k.value: v for k, v in c.highlights.items()},
                     "weaknesses": {k.value: v for k, v in c.weaknesses.items()},
                 } for c in state["scorecards"]])
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "verdict": verdict}

    return node
