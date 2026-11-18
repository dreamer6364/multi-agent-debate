"""辩手环节节点（SPEC §4.1 环节 2~8）。

发言节奏：陈词类环节每人一次发言（超预算句界截断）；质询/攻辩为问答对循环；
自由辩论双池分别计时、交替短打（硬截断，保证池耗尽语义确定）。
"""
from __future__ import annotations

from ..agents.context import build_debater_messages, estimate_tokens
from ..rag.retriever import detect_topic_domain, render_evidence
from ..llm.gateway import ContentFiltered, LLMExhausted, LLMResult, strip_tool_artifacts
from ..procedure import PHASE_NAMES, PHASE_NUMBERS, minutes_for
from ..runtime import DebateAborted, DebateContext
from ..schema import Camp, Speech
from ..sim.conduct import (
    FollowupLedger,
    flag_unverified_claims,
    has_interrupt_marker,
    is_hard_evasion,
    is_repetitive,
    scan,
    strip_forbidden_format,
    strip_interrupt_marker,
    strip_stage_tags,
)
from ..sim.digest import Digest, extract_excerpts, merge_digest, render_digest
from ..sim.interrupt import InterruptLedger, apply_interrupt_to_answer, parse_interrupt
from ..sim.timer import (
    NOTE_TIME_UP,
    NOTE_WARNING,
    FreeDebateClock,
    TurnClock,
    truncate_to_sentence,
    words_for,
)
from ..tools.agent_tools import run_debater_with_tools, tools_enabled_for
import re as _re

# v1.5 辩论纪律指令（治用户报告的五大问题：流程/论据/逻辑/复读）
_NO_INTERRUPT_RULE = (
    "【本环节无打断权】禁止使用[打断]标记；禁止模拟抢话或以'请正面回答'的质询口吻施压——"
    "那属于质询环节的动作。请完整、按顺序陈述本方观点。"
)
_EVIDENCE_RULES = (
    "【证据使用规范】①引用前检验证据与辩题的适用域是否匹配，"
    "禁止跨域硬套（如把商业/技术理论直接嫁接到情感、人际议题）；"
    "②描述性研究结论（如认知偏差）只用于揭示人的局限，不得直接推出价值选择结论；"
    "③无样本与方法的网络调查不得作为核心论据；④同一来源全场至多引用两次，优先轮换论据。"
)

# v1.8 表达多样性：切入点风格池——按"该辩手已发言次数"轮换，杜绝每条发言同一套路
_STYLE_ANGLES = (
    "从一个具体的生活场景或案例切入",
    "从一个数据、事实或研究结论切入",
    "从对方论证的隐含前提出发追问",
    "用一个类比或思想实验展开",
    "从判准（本题胜负标准）的争夺切入",
    "把对方立场推到极端情形做归谬",
    "从关键概念的辨析（X 与 Y 的边界）切入",
    "抓住对方原话中的矛盾点展开",
    "从利益相关方（谁受益、谁受损）切入",
    "从时间维度（短期代价 vs 长期收益）切入",
)

_ANTI_TEMPLATE_RULE = (
    "【表达多样性（硬性要求）】所有措辞由你临场组织：禁止照搬角色文件中的任何示例句式；"
    "禁止与你本场已有发言使用相同的开头短语或相同行文结构；"
    "禁止机械套用'首先/其次/最后''综上所述'的三段式模板；"
    "同一场比赛中不得复述本方任何人已说过的例证与金句。"
)

# v2.9.1 发言格式防线：免费档模型偶发把辩手发言输出成 JSON/评分卡（实测整场仅此一次，
# 但两条结辩同时中招）。辩手发言永远不可能是 JSON——凡以 { [ 开头的一律拦截：
# 带纠正指令重试一次，仍不合格则降级占位，绝不让原始 JSON 上屏。
# 注意：[打断] 标记也以 [ 开头，但那是合法质询动作（后续流程会剥离），必须豁免。
_JSON_LEAD = _re.compile(r"^[\[{]")
_JSON_RETRY_NOTE = (
    "【重试要求】你上一条输出是 JSON/结构化数据，不是辩论发言，已作废。"
    "请重新输出辩论发言正文：中文自然语言段落，像真实辩手一样陈词；"
    "严禁输出 JSON、代码、表格、字段名或任何结构化数据。"
)


def _looks_like_json(text: str) -> bool:
    """辩手发言的 JSON 粗判：以 { [ 开头即视为格式异常（正常发言永不如此开头）。"""
    t = (text or "").lstrip()
    if t.startswith("[打断"):
        return False  # 质询环节的合法打断标记，非 JSON
    return bool(_JSON_LEAD.match(t))


def _style_hint(ctx: DebateContext, speaker: str) -> str:
    """按辩手已发言次数轮换切入点提示 + 反模板硬约束（v1.8 治语言固定）。"""
    used = sum(1 for s in ctx.speeches if s.speaker == speaker)
    angle = _STYLE_ANGLES[used % len(_STYLE_ANGLES)]
    return f"【本条切入建议】{angle}（可临场调整）。{_ANTI_TEMPLATE_RULE}"


def _dynamic_evidence(ctx: DebateContext, spec, recent: list[Speech]):
    """v1.8 动态证据：按辩题+对方最近发言构造查询逐条检索（不同发言吃到不同证据卡）。

    检索不可用或无结果时回落全场预注入的固定证据（v1.4 行为）。
    """
    fallback = (ctx.evidence or {}).get(spec.camp, [])
    if ctx.retriever is None:
        return fallback
    last_opponent = next(
        (s.text for s in reversed(recent) if s.camp is not spec.camp and s.camp is not Camp.NEUTRAL),
        "",
    )
    query = f"{ctx.topic.title} {last_opponent[:80]}"
    domain = detect_topic_domain(f"{ctx.topic.title} {ctx.topic.pro_stance} {ctx.topic.con_stance}")
    try:
        cards = ctx.retriever.search(query, top_k=3, topic_domain=domain)
    except Exception:  # noqa: BLE001 —— 检索失败静默回落
        return fallback
    return cards or fallback

def _book_names(cards) -> list[str]:
    """从证据卡来源提取书名《…》（引用记账用）。"""
    names = []
    for c in cards:
        for m in _re.findall(r"《[^》]+》", c.source):
            names.append(m)
    return names

def _evidence_sources(ctx: DebateContext) -> list[str]:
    """平台注入的全部证据来源（双方并集，P2-4 数据诚信线索的白名单）。"""
    sources: list[str] = []
    for cards in (ctx.evidence or {}).values():
        for card in cards or []:
            src = (getattr(card, "source", "") or "").strip()
            if src and src not in sources:
                sources.append(src)
    return sources

def _usage_line(ctx, camp_cards) -> str:
    """引用纪律注入：本场已被引用 ≥2 次的来源提示勿再用。"""
    over = [name for name, n in ctx.evidence_usage.items() if n >= 2]
    if not over:
        return ""
    return f"【引用纪律】以下来源本场已被引用≥2次，请勿再用：{'、'.join(dict.fromkeys(over))}。"


def _timer_events(ctx: DebateContext, speaker: str | None, notes: list[str]) -> None:
    """把计时旁注映射为 timer 事件（SPEC §6.2：type ∈ warning/time_up）。"""
    for note in notes:
        if note == NOTE_WARNING:
            ctx.emit("timer", type="warning", speaker=speaker)
        elif note == NOTE_TIME_UP:
            ctx.emit("timer", type="time_up", speaker=speaker)
        elif note.endswith("时间用尽"):
            ctx.emit("timer", type="time_up", speaker=None)  # 池耗尽（正方/反方时间用尽）

#: 问答对之间的最小余量：低于此字数不再开启新的一问
MIN_TURN_WORDS = 60

MONOLOGUE_SPEAKERS: dict[str, list[str]] = {
    "statement": ["pro-1", "con-1"],
    "rebuttal": ["con-2", "pro-2"],
    "attack_summary": ["pro-3", "con-3"],
    "closing": ["con-4", "pro-4"],
}

CROSS_GROUPS = [("con-2", "pro-1"), ("pro-2", "con-1")]
ATTACK_GROUPS = [
    ("pro-3", ["con-1", "con-2", "con-4"]),
    ("con-3", ["pro-1", "pro-2", "pro-4"]),
]
# v1.5：二三辩为自由辩主力先手，一四辩补位（贴近真实分工：四辩保存体力结辩，一辩已完成立论）
FREE_ROTATION = {
    Camp.PRO: ["pro-2", "pro-3", "pro-1", "pro-4"],
    Camp.CON: ["con-2", "con-3", "con-1", "con-4"],
}


def _mono_instruction(key: str) -> str:
    if key == "statement":
        return ("你正在开篇立论：下定义（争夺定义权）、立判准（本题胜负标准）、铺 2~3 个论点（论点→论据→论证完整展开）。"
                "概念边界必须清晰：明确'我方主张 X ≠ Y'，把己方立场与易被混淆的极端形态区分开。")
    if key == "rebuttal":
        return ("你正在驳论：按“对方说 X → 但事实上/逻辑上/价值上……”逐条拆解对方一辩立论，并补强己方薄弱处。"
                "每次拆解必须给出中间论证链条（A 之所以推出问题，是因为…），禁止标签化跳跃（如直接给对方贴'滑坡''赌博'标签而不展开机理）。")
    if key == "attack_summary":
        return "你正在做攻辩小结：向评委归纳本方盘问的得分点，逐条点名对方暴露的问题。"
    return ("你正在总结陈词：复盘交锋→归纳对方谬误（引用原话举证）→重申己方框架→价值升华。不得引入新论点。"
            "归纳谬误时先复述对方原话，再指认该谬误属于哪一方——归属方（正/反）必须与实际发言方一致，严禁写反。")


def _compress_half(label: str, text: str) -> str:
    """合并期超长的兜底压缩：保留前一半（交锋点/谬误指控仅压缩、不删条目）。"""
    return text[: len(text) // 2] if len(text) > 60 else text


def _update_digest(ctx: DebateContext, state: dict, new_speeches: list[Speech]) -> Digest:
    columns = ctx.compressor(new_speeches) if new_speeches else {}
    excerpts = extract_excerpts(new_speeches)
    return merge_digest(
        state["digest"], columns, excerpts,
        max_chars=ctx.config.context.digest_max_chars, compress=_compress_half,
    )


def next_seq(speeches: list[Speech]) -> int:
    """发言序号分配：取已有最大序号 +1。

    v2.2：不再用 ``len(...)+1``——历史数据里出现缺号/重号时，len 会算出已被占用的
    序号，落库直接撞 ``speeches.uq_debate_seq``（用户侧表现为"评审未能完成：
    Duplicate entry ... for key 'speeches.uq_debate_seq'"）。按 max+1 分配天然单调。
    """
    return max((s.seq for s in speeches), default=0) + 1


def _emit(ctx: DebateContext, new: list[Speech], speech: Speech) -> None:
    new.append(speech)
    ctx.speeches.append(speech)
    if ctx.on_speech:
        ctx.on_speech(speech)
    # v2.7：每条发言定稿后推送最新一份复盘泳道总结（swimlane SSE 事件）
    # 轻量测试桩（SimpleNamespace）没有该方法，跳过即可
    if hasattr(ctx, "track_speech"):
        ctx.track_speech(speech)


def _ask(
    ctx: DebateContext,
    *,
    speaker: str,
    phase: int,
    phase_name: str,
    instruction: str,
    cap_words: int,
    hard_cut: bool,
    recent: list[Speech],
    digest_text: str,
    allow_interrupt_marker: bool = False,
    phase_key: str | None = None,
) -> Speech:
    """调用一名辩手/主席的 LLM，产出一条发言（含守则扫描、截断、降级占位）。

    v1.8：白名单环节走 Agent 工具循环（LLM 主动调 RAG/联网/Elenchus 自检共同生成）；
    其余环节保持流式单次调用。两种路径都吃动态证据与表达多样性指令。
    """
    config = ctx.config
    spec = ctx.agents[speaker]
    seq = next_seq(recent)
    if cap_words <= 0:  # 防御性出口：调用方保证预算为正
        raise DebateAborted(f"{speaker} 的发言预算非正: {cap_words}")
    camp_cards = _dynamic_evidence(ctx, spec, recent)
    extra_rules = _EVIDENCE_RULES + _usage_line(ctx, camp_cards)
    if not allow_interrupt_marker:
        extra_rules += _NO_INTERRUPT_RULE
    full_instruction = f"{instruction}\n{extra_rules}{_style_hint(ctx, speaker)}（本次发言预算约 {cap_words} 字）"
    evidence_text = render_evidence(camp_cards)
    messages = build_debater_messages(
        spec=spec,
        topic=ctx.topic,
        digest_text=digest_text,
        recent=recent,
        evidence_text=evidence_text,
        instruction=full_instruction,
        recent_k=config.context.recent_speeches,
        max_input_tokens=config.context.max_input_tokens,
    )
    holder = {"attempt": 0, "streamed": False}
    evidence_degraded = False  # ISS-015 #3：去证据回退成功时置位，落旁注"证据降级"
    try:
        if tools_enabled_for(ctx, phase_key):
            # 工具循环路径（v1.8）：非流式，LLM 可先调工具取材/自检再输出正文；
            # 内部任何失败已回落普通调用，此处再兜一层异常出口
            try:
                result, _used = run_debater_with_tools(
                    ctx, messages=messages, instruction=full_instruction, remaining_words=cap_words,
                )
            except LLMExhausted:
                raise
            except Exception:  # noqa: BLE001
                result = ctx.gateway.complete(
                    messages, tier="debater",
                    temperature=config.model.temperature.debater,
                    max_tokens=int(cap_words * 1.6) + 64,
                )
            ctx.degradation_streak = 0
        elif ctx.on_delta is not None:
            # 流式路径（v2.1 speech_delta）：边生成边推送；attempt 变化先发重置信号（delta=""）
            holder["streamed"] = True

            def _note_attempt(attempt: int) -> None:
                holder["attempt"] = attempt
                ctx.on_delta(seq, "", attempt, speaker, spec.camp.value)

            chunks: list[str] = []
            for chunk in ctx.gateway.stream(
                messages, tier="debater",
                temperature=config.model.temperature.debater,
                max_tokens=int(cap_words * 1.6) + 64,
                on_attempt=_note_attempt,
            ):
                chunks.append(chunk)
                ctx.on_delta(seq, chunk, holder["attempt"], speaker, spec.camp.value)
            raw_text = "".join(chunks)
            raw_text = strip_tool_artifacts(raw_text)  # 双保险：流式增量已过滤，终稿再剥一次
            prompt_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
            result = LLMResult(text=raw_text, prompt_tokens=prompt_tokens,
                               completion_tokens=len(raw_text))
        else:
            result = ctx.gateway.complete(
                messages, tier="debater",
                temperature=config.model.temperature.debater,
                max_tokens=int(cap_words * 1.6) + 64,
            )
        ctx.degradation_streak = 0
    except ContentFiltered:
        # ISS-015 #3：证据注入可能命中供应商内容黑名单（黑盒且会演进，其余卡片未逐一探过）——
        # 去证据重建 messages 重试一次：成功则本条发言正常上屏（旁注"证据降级"），
        # 仍失败或本就无证据可去才落占位降级，单点误拦不再熔断整场（SPEC §4.9）。
        result = None
        if camp_cards:
            try:
                result = ctx.gateway.complete(
                    build_debater_messages(
                        spec=spec, topic=ctx.topic, digest_text=digest_text, recent=recent,
                        evidence_text="", instruction=full_instruction,
                        recent_k=config.context.recent_speeches,
                        max_input_tokens=config.context.max_input_tokens,
                    ),
                    tier="debater",
                    temperature=config.model.temperature.debater,
                    max_tokens=int(cap_words * 1.6) + 64,
                )
            except LLMExhausted:  # 回退后仍被拦（过滤源不只证据）→ 走占位
                result = None
        if result is None:
            ctx.record_degradation()
            ctx.emit("error", message="LLM 降级：该辩手发言被内容过滤拦截")
            placeholder = "[系统] 该辩手发言被内容过滤拦截"
            return Speech(
                seq=seq, phase=phase, phase_name=phase_name, speaker=speaker, camp=spec.camp,
                text=placeholder, side_notes=["LLM 降级", "内容过滤拦截"], word_count=len(placeholder),
            )
        evidence_degraded = True
        ctx.degradation_streak = 0
    except LLMExhausted:
        ctx.record_degradation()
        ctx.emit("error", message="LLM 降级：该辩手超时未发言")
        placeholder = "[系统] 该辩手超时未发言"
        return Speech(
            seq=seq, phase=phase, phase_name=phase_name, speaker=speaker, camp=spec.camp,
            text=placeholder, side_notes=["LLM 降级"], word_count=len(placeholder),
        )
    text, _ = strip_forbidden_format(result.text.strip())
    if _looks_like_json(text):
        # ISS-012 BE-4：流式分支已把原始 JSON 逐 chunk 推给前端，重试/降级前先发
        # attempt+1 的重置信号（delta=""）清空打字卡，避免脏文本与终稿不一致。
        if ctx.on_delta is not None and holder["streamed"]:
            ctx.on_delta(seq, "", holder["attempt"] + 1, speaker, spec.camp.value)
        # v2.9.1 格式防线：带纠正指令重试一次；仍输出 JSON 则降级占位，绝不上屏原始数据
        retry_messages = build_debater_messages(
            spec=spec, topic=ctx.topic, digest_text=digest_text, recent=recent,
            # 已走去证据回退的发言：JSON 纠偏重试不得再注入已知被拦的证据源
            evidence_text="" if evidence_degraded else evidence_text,
            instruction=f"{full_instruction}\n{_JSON_RETRY_NOTE}",
            recent_k=config.context.recent_speeches,
            max_input_tokens=config.context.max_input_tokens,
        )
        try:
            retry = ctx.gateway.complete(
                retry_messages, tier="debater",
                temperature=config.model.temperature.debater,
                max_tokens=int(cap_words * 1.6) + 64,
            )
            text, _ = strip_forbidden_format(retry.text.strip())
            result = retry
        except LLMExhausted:
            raise
        except Exception:  # noqa: BLE001 —— 重试失败走占位
            pass
        if _looks_like_json(text):
            ctx.emit("error", message="LLM 格式异常：该辩手输出了无效内容")
            placeholder = "[系统] 该辩手输出了无效内容（格式异常），本条发言作废"
            return Speech(
                seq=seq, phase=phase, phase_name=phase_name, speaker=speaker, camp=spec.camp,
                text=placeholder, side_notes=["LLM 格式异常"], word_count=len(placeholder),
            )
    notes = scan(text, config.conduct.abuse_keywords)
    if evidence_degraded:
        notes.append("证据降级")  # ISS-015 #3：本条未用证据卡（过滤回退），评委/导出可见
    # P2-4 数据诚信线索：精确百分比/研究结论句若未标注平台白名单来源 → 旁注"数据待查"交评委核验
    notes.extend(flag_unverified_claims(text, _evidence_sources(ctx)))
    if not allow_interrupt_marker:
        if has_interrupt_marker(text):
            notes.append("违规：非质询环节打断")
        text = strip_stage_tags(strip_interrupt_marker(text))  # 守则六 + G8 舞台标记
    if hard_cut:
        if len(text) > cap_words:
            text = text[:cap_words]
    else:
        text, _ = truncate_to_sentence(text, cap_words)
    for name in _book_names(camp_cards):
        if name in text:
            ctx.evidence_usage[name] = ctx.evidence_usage.get(name, 0) + 1
    return Speech(
        seq=seq, phase=phase, phase_name=phase_name, speaker=speaker, camp=spec.camp,
        text=text, side_notes=notes, word_count=len(text),
        tokens=result.prompt_tokens + result.completion_tokens,
    )


def make_monologue_node(phase_key: str, ctx: DebateContext):
    phase = PHASE_NUMBERS[phase_key]
    phase_name = PHASE_NAMES[phase_key]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base, new = state["transcript"], []
        digest_text = render_digest(state["digest"])
        budget = words_for(minutes_for(phase_key, ctx.config), ctx.config.procedure.speed_cpm)
        ctx.emit("phase_start", phase=phase, name=phase_name,
                 actors=list(MONOLOGUE_SPEAKERS[phase_key]), budget_words=budget)
        if budget <= 0:
            # ISS-012 BE-6：环节时长配 0 = 该环节不发言。保留成对的环节戳维持时间线，
            # 但不调 LLM、不产 0 字发言；此前 cap_words<=0 会直接 DebateAborted 熔断整场。
            ctx.emit("phase_end", phase=phase)
            return {"transcript": base, "digest": state["digest"]}
        for speaker in MONOLOGUE_SPEAKERS[phase_key]:
            ctx.check_cancel()
            clock = TurnClock(budget, ctx.config.procedure.speed_cpm)
            speech = _ask(
                ctx, speaker=speaker, phase=phase, phase_name=phase_name,
                instruction=_mono_instruction(phase_key), cap_words=budget, hard_cut=False,
                recent=base + new, digest_text=digest_text, phase_key=phase_key,
            )
            clock_notes = clock.consume(speech.word_count)
            speech.side_notes.extend(clock_notes)
            _timer_events(ctx, speaker, clock_notes)
            _emit(ctx, new, speech)
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "digest": _update_digest(ctx, state, new)}

    return node


def make_cross_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["cross_examination"], PHASE_NAMES["cross_examination"]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base, new = state["transcript"], []
        digest_text = render_digest(state["digest"])
        budget = words_for(minutes_for("cross_examination", ctx.config), ctx.config.procedure.speed_cpm)
        ctx.emit("phase_start", phase=phase, name=phase_name,
                 actors=[q for pair in CROSS_GROUPS for q in pair], budget_words=budget)
        for questioner, respondent in CROSS_GROUPS:
            ctx.check_cancel()
            clock = TurnClock(budget, ctx.config.procedure.speed_cpm)
            ledger = InterruptLedger(ctx.config.conduct.max_interrupts_per_answer)
            followups = FollowupLedger(threshold=3)  # P0-2 回应义务：同一问题第 3 次追问计数
            last_answer: Speech | None = None
            while clock.remaining >= MIN_TURN_WORDS:
                ctx.check_cancel()
                question = _ask(
                    ctx, speaker=questioner, phase=phase, phase_name=phase_name,
                    instruction="你正在质询：提出简短、指向明确的问题，一次一问。必要时以 [打断] 加理由打断对方冗长回答。",
                    cap_words=clock.remaining, hard_cut=True, recent=base + new, digest_text=digest_text,
                    allow_interrupt_marker=True, phase_key="cross_examination",
                )
                clean, reason = parse_interrupt(question.text)
                question.text = clean
                question.word_count = len(clean)
                asks = followups.register_ask(clean)  # P0-2 回应义务：本问题被追问第几次
                if reason is not None:
                    if last_answer is not None and reason and ledger.try_consume(last_answer.seq):
                        apply_interrupt_to_answer(last_answer, reason)
                        # P1-1 打断实时修正：带上被打断发言的 seq 与截断后文本——
                        # 该回答的 speech 事件早已推送（完整文本），前端凭此事件就地截断已渲染内容，
                        # 不必等到终态 upsert 才看到"被打断"（SPEC §4.3 模拟口径的直播侧落地）。
                        ctx.emit("interrupt", by=questioner, reason=reason,
                                 seq=last_answer.seq, text=last_answer.text,
                                 side_notes=list(last_answer.side_notes),
                                 word_count=last_answer.word_count)
                    else:
                        question.side_notes.append("打断违规（超限或未说明理由）")
                question_clock_notes = clock.consume(question.word_count)
                question.side_notes.extend(question_clock_notes)
                _timer_events(ctx, questioner, question_clock_notes)
                _emit(ctx, new, question)
                if clock.remaining <= 0:
                    break
                answer = _ask(
                    ctx, speaker=respondent, phase=phase, phase_name=phase_name,
                    instruction=(
                        "你正在被质询，只答不问。防守要领：①先正面回应问题本身（是/否/程度），"
                        "再给一句理由，不要绕开提问词；②守住我方定义与判准——问题若暗含错误前提，"
                        "先点破前提错在哪里再回答；③只作最小必要让步（「退一步讲」），"
                        "让步不得动摇判准；④对方问题超出辩题范围时，明确指出并拉回本方判准作答。"
                        "回答须简洁、正面、不回避。"
                    ),
                    cap_words=clock.remaining, hard_cut=True, recent=base + new, digest_text=digest_text,
                    phase_key="cross_examination",
                )
                # P0-2 回应义务（守则四/G6）：同一问题第 3 次追问仍无对应回答 → 旁注"回避追问"
                if followups.is_unanswered(answer.text, asks):
                    answer.side_notes.append("回避追问")
                followups.register_answer(answer.text)
                answer_notes = clock.consume(answer.word_count)
                answer.side_notes.extend(answer_notes)
                _timer_events(ctx, respondent, answer_notes)
                last_answer = answer
                _emit(ctx, new, answer)
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "digest": _update_digest(ctx, state, new)}

    return node


def make_attack_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["attack"], PHASE_NAMES["attack"]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base, new = state["transcript"], []
        digest_text = render_digest(state["digest"])
        budget = words_for(minutes_for("attack", ctx.config), ctx.config.procedure.speed_cpm)
        actors = [q for q, _ in ATTACK_GROUPS] + [t for _, targets in ATTACK_GROUPS for t in targets]
        ctx.emit("phase_start", phase=phase, name=phase_name, actors=actors, budget_words=budget)
        for questioner, targets in ATTACK_GROUPS:
            ctx.check_cancel()
            clock = TurnClock(budget, ctx.config.procedure.speed_cpm)
            followups = FollowupLedger(threshold=3)  # P0-2 回应义务：同一问题第 3 次追问计数
            for target in targets:
                ctx.check_cancel()
                if clock.remaining < MIN_TURN_WORDS:
                    break
                question = _ask(
                    ctx, speaker=questioner, phase=phase, phase_name=phase_name,
                    instruction="你正在攻辩盘问：一次一问，可设计二难陷阱题、归谬题、数据逼问题；被盘问方只能回答，不能反问。",
                    cap_words=clock.remaining, hard_cut=True, recent=base + new, digest_text=digest_text,
                    phase_key="attack",
                )
                asks = followups.register_ask(question.text)  # P0-2 回应义务：本问题被追问第几次
                # v2.10：提问方同样吃这一组的共享时间池，其计时旁注须映射为 timer 事件
                # （此前只落旁注不发事件，直播计时条在攻辩环节对提问方不动——与质询环节不一致）
                question_clock_notes = clock.consume(question.word_count)
                question.side_notes.extend(question_clock_notes)
                _timer_events(ctx, questioner, question_clock_notes)
                _emit(ctx, new, question)
                if clock.remaining <= 0:
                    break
                answer = _ask(
                    ctx, speaker=target, phase=phase, phase_name=phase_name,
                    instruction=(
                        "你正在被盘问，只能回答、不能反问。防守要领：①直接回应问题本身，"
                        "不换话题、不反问对方；②二难题被夹击时，指出两难前提不成立（还有第三种可能）"
                        "或选择对己方判准损害最小的一项并说明理由；③归谬题被推演时，"
                        "指出对方推演跳步之处，守住原命题；④数据逼问答不出时，"
                        "如实说明「我方暂无该精确数据」并给限定语与常识性论证，禁止编造。"
                    ),
                    cap_words=clock.remaining, hard_cut=True, recent=base + new, digest_text=digest_text,
                    phase_key="attack",
                )
                # P0-2 回应义务（守则四/G6）：同一问题第 3 次追问仍无对应回答 → 旁注"回避追问"
                if followups.is_unanswered(answer.text, asks):
                    answer.side_notes.append("回避追问")
                followups.register_answer(answer.text)
                answer_notes = clock.consume(answer.word_count)
                answer.side_notes.extend(answer_notes)
                _timer_events(ctx, target, answer_notes)
                _emit(ctx, new, answer)
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "digest": _update_digest(ctx, state, new)}

    return node


def make_free_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["free_debate"], PHASE_NAMES["free_debate"]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base, new = state["transcript"], []
        digest_text = render_digest(state["digest"])
        clock = FreeDebateClock(
            minutes_per_camp=minutes_for("free_debate", ctx.config),
            speed_cpm=ctx.config.procedure.speed_cpm,
            turn_cap_minutes=ctx.config.procedure.free_debate_turn_cap,
        )
        rotation_index = {Camp.PRO: 0, Camp.CON: 0}
        turn_count = {Camp.PRO: 0, Camp.CON: 0}  # ISS-014 B-2：每方实际发言回合（含连讲）
        max_turns = max(1, int(getattr(ctx.config.procedure, "free_debate_max_turns", 8)))
        camp = Camp.PRO  # 正方先手（SPEC §4.1）
        # 防御性总上限：每方上限 ×2 + 少量换手余量；正常由 turn_count/字数池先收敛
        guard_limit = max_turns * 2 + 8
        guard = 0
        ctx.emit("phase_start", phase=phase, name=phase_name,
                 actors=FREE_ROTATION[Camp.PRO] + FREE_ROTATION[Camp.CON],
                 budget_words=clock.pools[Camp.PRO].remaining)
        while not clock.finished and guard < guard_limit:
            guard += 1
            ctx.check_cancel()
            if clock.pools[camp].exhausted or turn_count[camp] >= max_turns:
                camp = FreeDebateClock.other(camp)
                continue
            speaker = FREE_ROTATION[camp][rotation_index[camp] % len(FREE_ROTATION[camp])]
            rotation_index[camp] += 1
            turn_count[camp] += 1
            cap = min(clock.turn_cap_words, clock.pools[camp].remaining)
            if cap <= 0:  # ISS-012 BE-6：单轮上限/时间池配 0 时跳过该轮，不熔断整场
                break
            previous = [sp.text for sp in base + new if sp.speaker == speaker and sp.phase == phase]
            speech = _ask(
                ctx, speaker=speaker, phase=phase, phase_name=phase_name,
                instruction=("你正在自由辩论：这是全场对抗最激烈的环节。把话说完整再交棒——"
                             "观点、依据、对对方上一轮的具体回击一次说透，不要为省字数半途收束；"
                             "篇幅按内容需要自然伸缩，短打可以三两句，交锋胶着时充分展开。"
                             "反问句是自由辩的优先武器（如'难道……吗？''若按对方逻辑，……又将如何？'），"
                             "直击对方要害、增强对抗性，对方含糊其辞或自相矛盾时尤其有效；"
                             "也可以连续追问，逼对方正面回应。是否使用、用几个，由你按交锋需要自行判断，"
                             "不必每轮都用。"
                             "禁止在发言中模拟打断或抢话；"
                             "禁止重复本方已陈述过的论据原句（会被标注复读），"
                             "每轮要么深挖对方上一发言的具体漏洞，要么推进新角度。"),
                cap_words=cap, hard_cut=True, recent=base + new, digest_text=digest_text,
                phase_key="free_debate",
            )
            if is_hard_evasion(speech.text, previous):
                speech.side_notes.append("回避追问")
            team_texts = [sp.text for sp in base + new if sp.camp == camp and sp.phase == phase]
            # P1-3 复读判定收紧：阈值提高到 25 字（15 字只是半句话，回扣己方判准/定义
            # 几乎必然命中，误伤率高）；且若本条同时引用了对方原话（≥12 字重叠），
            # 属于"有交锋的复述"而非复读。此旁注仅作前端提示——评委侧的复读信号
            # 由 QC 流式质检的 8-gram 重叠率规则提供（qc_stream，更精确且带相似度）。
            opponent_texts = [sp.text for sp in base + new if sp.camp is not camp and sp.phase == phase]
            quotes_opponent = is_repetitive(speech.text, opponent_texts, min_overlap=12)
            if not quotes_opponent and is_repetitive(speech.text, team_texts):
                speech.side_notes.append("复读论据")
            pool_notes = clock.consume(camp, speech.word_count)
            speech.side_notes.extend(pool_notes)
            _timer_events(ctx, speaker, pool_notes)
            _emit(ctx, new, speech)
            camp = FreeDebateClock.other(camp)
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "digest": _update_digest(ctx, state, new)}

    return node


def make_statement_node(ctx: DebateContext):
    return make_monologue_node("statement", ctx)


def make_rebuttal_node(ctx: DebateContext):
    return make_monologue_node("rebuttal", ctx)


def make_summary_node(ctx: DebateContext):
    return make_monologue_node("attack_summary", ctx)


def make_closing_node(ctx: DebateContext):
    return make_monologue_node("closing", ctx)
