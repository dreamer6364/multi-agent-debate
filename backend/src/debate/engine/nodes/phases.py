"""辩手环节节点（SPEC §4.1 环节 2~8）。

发言节奏：陈词类环节每人一次发言（超预算句界截断）；质询/攻辩为问答对循环；
自由辩论双池分别计时、交替短打（硬截断，保证池耗尽语义确定）。
"""
from __future__ import annotations

from ..agents.context import build_debater_messages, estimate_tokens
from ..llm.gateway import LLMExhausted, LLMResult
from ..procedure import PHASE_NAMES, PHASE_NUMBERS, minutes_for
from ..runtime import DebateAborted, DebateCancelled, DebateContext
from ..schema import Camp, Speech
from ..sim.conduct import (
    has_interrupt_marker,
    is_hard_evasion,
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
FREE_ROTATION = {
    Camp.PRO: ["pro-1", "pro-2", "pro-3", "pro-4"],
    Camp.CON: ["con-1", "con-2", "con-3", "con-4"],
}


def _mono_instruction(key: str) -> str:
    if key == "statement":
        return "你正在开篇立论：下定义（争夺定义权）、立判准（本题胜负标准）、铺 2~3 个论点（论点→论据→论证完整展开）。"
    if key == "rebuttal":
        return "你正在驳论：按“对方说 X → 但事实上/逻辑上/价值上……”逐条拆解对方一辩立论，并补强己方薄弱处。"
    if key == "attack_summary":
        return "你正在做攻辩小结：向评委归纳本方盘问的得分点，逐条点名对方暴露的问题。"
    return "你正在总结陈词：复盘交锋→归纳对方谬误（引用原话举证）→重申己方框架→价值升华。不得引入新论点。"


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


def _emit(ctx: DebateContext, new: list[Speech], speech: Speech) -> None:
    new.append(speech)
    ctx.speeches.append(speech)
    if ctx.on_speech:
        ctx.on_speech(speech)


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
) -> Speech:
    """调用一名辩手/主席的 LLM，产出一条发言（含守则扫描、截断、降级占位）。"""
    config = ctx.config
    spec = ctx.agents[speaker]
    seq = len(recent) + 1
    if cap_words <= 0:  # 防御性出口：调用方保证预算为正
        raise DebateAborted(f"{speaker} 的发言预算非正: {cap_words}")
    messages = build_debater_messages(
        spec=spec,
        topic=ctx.topic,
        digest_text=digest_text,
        recent=recent,
        instruction=f"{instruction}（本次发言预算约 {cap_words} 字）",
        recent_k=config.context.recent_speeches,
        max_input_tokens=config.context.max_input_tokens,
    )
    try:
        if ctx.on_delta is not None:
            # 流式路径（v2.1 speech_delta）：边生成边推送；attempt 变化先发重置信号（delta=""）
            holder = {"attempt": 0}

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
    except LLMExhausted as exc:
        ctx.record_degradation()
        ctx.emit("error", message="LLM 降级：该辩手超时未发言")
        placeholder = "[系统] 该辩手超时未发言"
        return Speech(
            seq=seq, phase=phase, phase_name=phase_name, speaker=speaker, camp=spec.camp,
            text=placeholder, side_notes=["LLM 降级"], word_count=len(placeholder),
        )
    text, _ = strip_forbidden_format(result.text.strip())
    notes = scan(text, config.conduct.abuse_keywords)
    if not allow_interrupt_marker:
        if has_interrupt_marker(text):
            notes.append("违规：非质询环节打断")
        text = strip_stage_tags(strip_interrupt_marker(text))  # 守则六 + G8 舞台标记
    if hard_cut:
        if len(text) > cap_words:
            text = text[:cap_words]
    else:
        text, _ = truncate_to_sentence(text, cap_words)
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
        for speaker in MONOLOGUE_SPEAKERS[phase_key]:
            ctx.check_cancel()
            clock = TurnClock(budget, ctx.config.procedure.speed_cpm)
            speech = _ask(
                ctx, speaker=speaker, phase=phase, phase_name=phase_name,
                instruction=_mono_instruction(phase_key), cap_words=budget, hard_cut=False,
                recent=base + new, digest_text=digest_text,
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
            last_answer: Speech | None = None
            while clock.remaining >= MIN_TURN_WORDS:
                ctx.check_cancel()
                question = _ask(
                    ctx, speaker=questioner, phase=phase, phase_name=phase_name,
                    instruction="你正在质询：提出简短、指向明确的问题，一次一问。必要时以 [打断] 加理由打断对方冗长回答。",
                    cap_words=clock.remaining, hard_cut=True, recent=base + new, digest_text=digest_text,
                    allow_interrupt_marker=True,
                )
                clean, reason = parse_interrupt(question.text)
                question.text = clean
                question.word_count = len(clean)
                if reason is not None:
                    if last_answer is not None and reason and ledger.try_consume(last_answer.seq):
                        apply_interrupt_to_answer(last_answer, reason)
                        ctx.emit("interrupt", by=questioner, reason=reason)
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
                    instruction="你正在被质询：只答不问，回答简洁、正面、不回避。",
                    cap_words=clock.remaining, hard_cut=True, recent=base + new, digest_text=digest_text,
                )
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
            for target in targets:
                ctx.check_cancel()
                if clock.remaining < MIN_TURN_WORDS:
                    break
                question = _ask(
                    ctx, speaker=questioner, phase=phase, phase_name=phase_name,
                    instruction="你正在攻辩盘问：一次一问，可设计二难陷阱题、归谬题、数据逼问题；被盘问方只能回答，不能反问。",
                    cap_words=clock.remaining, hard_cut=True, recent=base + new, digest_text=digest_text,
                )
                question.side_notes.extend(clock.consume(question.word_count))
                _emit(ctx, new, question)
                if clock.remaining <= 0:
                    break
                answer = _ask(
                    ctx, speaker=target, phase=phase, phase_name=phase_name,
                    instruction="你正在被盘问：只能回答，不能反问；正面回应问题本身。",
                    cap_words=clock.remaining, hard_cut=True, recent=base + new, digest_text=digest_text,
                )
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
        camp = Camp.PRO  # 正方先手（SPEC §4.1）
        guard = 0
        ctx.emit("phase_start", phase=phase, name=phase_name,
                 actors=FREE_ROTATION[Camp.PRO] + FREE_ROTATION[Camp.CON],
                 budget_words=clock.pools[Camp.PRO].remaining)
        while not clock.finished and guard < 96:
            guard += 1
            ctx.check_cancel()
            if clock.pools[camp].exhausted:
                camp = FreeDebateClock.other(camp)
                continue
            speaker = FREE_ROTATION[camp][rotation_index[camp] % len(FREE_ROTATION[camp])]
            rotation_index[camp] += 1
            cap = min(clock.turn_cap_words, clock.pools[camp].remaining)
            previous = [s.text for s in base + new if s.speaker == speaker and s.phase == phase]
            speech = _ask(
                ctx, speaker=speaker, phase=phase, phase_name=phase_name,
                instruction="你正在自由辩论：短打快攻，一次只打一个点，单次发言不超过预算；必须接对方上一回合的招。",
                cap_words=cap, hard_cut=True, recent=base + new, digest_text=digest_text,
            )
            if is_hard_evasion(speech.text, previous):
                speech.side_notes.append("回避追问")
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
