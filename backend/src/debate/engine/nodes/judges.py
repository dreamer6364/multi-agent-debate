"""评委节点（phase 9）：3 位评委并行点评（ARCH D5），结构化输出 + 弃权降级（SPEC §4.7）。

v1.9.2 防卡死加固：
- Elenchus 质检给总预算（默认 90s）并逐条播报进度，不再"静默几分钟"；
- 评审期起守护心跳线程，每 judges.heartbeat_seconds 推一次 judging_progress（前端可显示耗时）；
- 评委 LLM 调用走自建线程池 + 单评委硬超时，完成一位立即播报；
- 节点内所有阻塞路径都有上限：质检、评审、落库文案，任何一处失败都按规则弃权推进。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..agents.context import build_judge_messages
from ..llm.gateway import LLMExhausted
from ..procedure import PHASE_NAMES, PHASE_NUMBERS
from ..runtime import DebateContext
from ..schema import CAMP_CN, Camp, ScoreCard, Speech
from ..scoring import ScoreParseError, abstain_card, dims_for_format, parse_scorecard
from ..tools import elenchus as elenchus_tool
from ..sim.digest import render_digest
from .phases import _emit, next_seq

JUDGE_FOCUS = {"judge-logic": "logic", "judge-content": "content", "judge-expression": "expression"}
# 质检兜底硬上限的余量：质检自身已有 total_budget，这一层只防"内部完全不返回"
ELENCHUS_GUARD_MARGIN = 10.0


def _judge_instruction(fmt) -> str:
    dims = dims_for_format(fmt)
    dims_text = "、".join(f"{k} {v} 分" for k, v in dims.items())
    return (
        "请输出你的评审结果，只输出一个 JSON 对象，字段："
        f'pro_scores 与 con_scores（六维，满分：{dims_text}）；'
        'vote（"pro" 或 "con"）；best_speaker（全场最佳辩手 role_id）；'
        "highlights 与 weaknesses（各含 pro/con 两侧，每侧 2~3 条，每条必须用引号引用具体发言原话）。"
        "你的侧重见角色文件；打分与点评结论必须自洽。"
        "硬性约束：highlights 与 weaknesses 四侧一律不允许留空、不允许写「无」或「没有明显亮点」——"
        "全场发言记录已完整提供，必然存在可点评的内容，你必须选出具体的部分来点评；"
        "每条点评 = 引用一句具体原话 + 说明理由（为什么成立/为什么有问题/为什么值得注意），"
        "只引用不解释视为不合格。"
    )


def _checklist(transcript: list[Speech]) -> str:
    phases = sorted({s.phase for s in transcript})
    return f"全场比赛共 {len(transcript)} 条发言，历经环节 {phases}。"


#: 纳入评委纪律记录的旁注关键词（P0-2）。
#: 命中即视为"该发言的纪律信号"，按发言人聚合后注入评委——表达评委的风度纪律分据此扣分。
#: 排除：合法打断"质询方打断：…"（属正当质询动作）、剩 30 秒提醒（非违规）、
#: LLM 降级/格式异常（系统故障，不得算在辩手头上）、"复读论据"（前端提示项；
#: 评委侧复读信号由 QC 流式质检的 8-gram 重叠率规则提供，更精确且带相似度）。
#: "数据待查"（P2-4）不是扣分项而是核验线索，交内容评委查证后自行判定。
_CONDUCT_MARKERS = (
    "违规：人身攻击", "违规：跳出角色", "打断违规", "回避追问", "时间到", "数据待查",
)


def collect_conduct_records(transcript: list[Speech]) -> str:
    """把全场所属纪律旁注聚合成可注入评委的清单（P0-2）。

    每条形如：`[环节 N｜辩手] 记录`。无任何记录时返回空串（不注入该段）。
    """
    lines: list[str] = []
    for speech in transcript:
        if speech.camp is Camp.NEUTRAL:
            continue  # 主席/评委/计时员旁注不构成辩手纪律记录
        for note in speech.side_notes or []:
            if any(marker in note for marker in _CONDUCT_MARKERS):
                lines.append(f"- [环节 {speech.phase}｜{CAMP_CN[speech.camp]}·{speech.speaker}] {note}")
    return "\n".join(lines)


def collect_evidence_sources(ctx) -> str:
    """平台注入给辩手的证据卡来源白名单（P0-2）：内容评委据此区分真证据与虚构引用。"""
    cards = []
    for camp_cards in (getattr(ctx, "evidence", None) or {}).values():
        cards.extend(camp_cards or [])
    sources = []
    for card in cards:
        name = (getattr(card, "source", "") or "").strip()
        if name and name not in sources:
            sources.append(name)
    return "；".join(f"《{s}》" if "《" not in s else s for s in sources)


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


def _first_sentence(text: str, limit: int = 60) -> str:
    """取第一句（自然句末标点优先），兜底点评的引用来源。"""
    text = re.sub(r"\s+", "", text or "")
    for sep in ("。", "！", "？", "；"):
        idx = text.find(sep)
        if 0 < idx < limit:
            return text[: idx + 1]
    return text[:limit]


def _rescue_commentary(card: ScoreCard, transcript: list[Speech], marks: list[dict]) -> None:
    """弃权卡兜底点评（v2.6）：评审环节不允许出现空白点评。

    点评内容不是编造的观点：亮点取该方最长的实质发言（立论主干），不足取机器质检
    的谬误标注（quote+explanation 都来自真实记录）；card.rescued 如实标记，
    前端据此展示"含系统兜底点评"。
    """
    for camp in (Camp.PRO, Camp.CON):
        pool = [s for s in transcript if s.camp is camp and s.text]
        if pool and not card.highlights.get(camp):
            core = max(pool, key=lambda s: len(s.text))
            quote = _first_sentence(core.text)
            if quote:
                card.highlights[camp] = [
                    f"“{quote}”——{core.speaker} 的这一论点构成本方立论主干（篇幅与论证密度最高）"
                ]
        fall = [m for m in marks if m.get("camp") == camp.value and m.get("quote")]
        if fall and not card.weaknesses.get(camp):
            card.weaknesses[camp] = [
                f"“{(m.get('quote') or '')[:60]}”——{m.get('name', '论证瑕疵')}："
                f"{(m.get('explanation') or '')[:60]}"
                for m in fall[:2]
            ]
    if any(card.highlights.values()) or any(card.weaknesses.values()):
        card.rescued = True


def _run_bounded(fn, budget: float):
    """在独立线程里跑质检，**有界等待**——即使 elenchus 内部彻底挂死也不拖住评委阶段。

    返回 (ok, value)：超时返回 (False, None)，异常返回 (False, None)。
    shutdown 用 wait=False：卡住的线程留作守护性泄漏，绝不阻塞调用方。
    """
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="elenchus-guard")
    future = None
    try:
        future = pool.submit(fn)
        return True, future.result(timeout=budget)
    except concurrent.futures.TimeoutError:
        if future is not None:
            future.cancel()
        return False, None
    except Exception:  # noqa: BLE001 —— 质检任何失败都降级为纯 LLM 评审
        return False, None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def make_judging_node(ctx: DebateContext):
    phase, phase_name = PHASE_NUMBERS["judging"], PHASE_NAMES["judging"]

    def node(state: dict) -> dict:
        ctx.check_cancel()
        base = state["transcript"]
        judge_ids = [rid for rid in ctx.agents if rid.startswith("judge-")][: ctx.config.judges.count]
        ctx.emit("phase_start", phase=phase, name=phase_name, actors=judge_ids, budget_words=0)
        ctx.emit("judging", judges=judge_ids)

        # ---- 进度播报（v1.9.2）：质检与评审期间的每一步都可见，前端据此显示"已评议 Xs" ----
        started = time.monotonic()
        progress = {"stage": "质检", "completed": 0}

        def emit_progress(*, stage: str | None = None, completed: int | None = None) -> None:
            if stage is not None:
                progress["stage"] = stage
            if completed is not None:
                progress["completed"] = completed
            try:
                ctx.emit("judging_progress", stage=progress["stage"], completed=progress["completed"],
                         total=len(judge_ids), elapsed=round(time.monotonic() - started, 1))
            except Exception:  # noqa: BLE001 —— 播报失败绝不影响评审
                pass

        heartbeat_stop = threading.Event()
        hb_interval = float(getattr(ctx.config.judges, "heartbeat_seconds", 10.0))

        def heartbeat() -> None:
            while not heartbeat_stop.wait(max(1.0, hb_interval)):
                emit_progress()

        hb_thread = threading.Thread(target=heartbeat, name="judging-heartbeat", daemon=True)
        hb_thread.start()

        digest_text = render_digest(state["digest"])
        checklist = _checklist(base)
        # P0-2 评委证据链：违规纪律记录 + 证据来源白名单（表达/内容评委据此打分，不再凭空判）
        conduct_records = collect_conduct_records(base)
        evidence_sources = collect_evidence_sources(ctx)

        # v1.6 Elenchus 机器论证分析：仅注入逻辑评委（工具检测 ≠ 评委结论）
        # v1.7：结构化 marks → SSE fallacy 事件 + state（落库挂载到发言）；任何工具失败降级为纯 LLM 评审
        # v1.9.2：总预算封顶 + 逐条进度播报——质检不再可能静默拖住整个评分环节
        # v2.4：质检与评委评审**并行**——质检线程开跑后评委立即开始；逻辑评委最多等
        #       QC_JOIN_WAIT 秒拿质检结论注入提示词，等不到就先评审（谬误标注完成后仍会
        #       发 fallacy 事件挂到质检报告）。评分环节总耗时从「质检 + 评审」压到
        #       「max(质检, 评审)」，所有等待都有界，质检挂死最多损失标注、绝不拖阶段。
        machine_analysis = ""
        machine_marks: list[dict] = []
        elenchus_cfg = ctx.config.tools.elenchus
        qc_enabled = bool(getattr(elenchus_cfg, "enabled", False)) and "judge-logic" in judge_ids
        qc_stream = getattr(ctx, "qc", None)  # v2.8.1 流式质检：辩论期间已逐条分析
        qc_budget = float(getattr(elenchus_cfg, "total_budget_seconds", 90.0))
        QC_JOIN_WAIT = min(  # v2.6 改为可配置：质检是进程内分析，通常秒级完成，20s 白等 rarely 值得
            qc_budget, float(getattr(ctx.config.judges, "qc_join_wait_seconds", 10.0)))
        QC_FINAL_WAIT = float(getattr(ctx.config.judges, "qc_final_wait_seconds", 10.0))
        qc_done = threading.Event()
        qc_holder: dict = {"ok": False, "outcome": None}
        qc_harvested = False
        qc_started = time.monotonic()

        def qc_worker() -> None:
            if qc_stream is not None:
                # 流式模式：辩论期间已逐条分析，这里只收割现成结果（join 等待在 harvest 内部，
                # _run_bounded 再兜一层底：即使 harvest 挂死也不拖评委）
                ok, outcome = _run_bounded(
                    lambda: qc_stream.harvest(QC_JOIN_WAIT),
                    QC_JOIN_WAIT + ELENCHUS_GUARD_MARGIN,
                )
                qc_holder["ok"], qc_holder["outcome"] = ok, outcome
                return
            try:
                ok, outcome = _run_bounded(
                    lambda: elenchus_tool.analyze_speeches_detailed(
                        [s for s in base if s.camp is not Camp.NEUTRAL],
                        per_camp=getattr(elenchus_cfg, "per_camp", 2),
                        check=getattr(elenchus_cfg, "check", "fallacy"),
                        sensitivity=getattr(elenchus_cfg, "sensitivity", "strict"),
                        timeout=getattr(elenchus_cfg, "timeout_seconds", 45.0),
                        mode=getattr(elenchus_cfg, "mode", "inproc"),
                        total_budget=qc_budget,
                        parallel=int(getattr(elenchus_cfg, "parallel", 2)),
                        on_progress=lambda done, total: emit_progress(
                            stage=f"质检 {min(done + 1, total)}/{total}"),
                    ),
                    qc_budget + ELENCHUS_GUARD_MARGIN,  # 兜底硬上限：预算 + 收尾余量
                )
                qc_holder["ok"], qc_holder["outcome"] = ok, outcome
            except Exception:  # noqa: BLE001 —— 质检任何失败都降级为纯 LLM 评审
                pass
            finally:
                qc_done.set()

        def collect_qc(wait: float) -> None:
            """有界等待并收割质检结果：拿到即发 fallacy 事件（幂等，只收割一次）。"""
            nonlocal machine_analysis, machine_marks, qc_harvested
            if not qc_enabled or qc_harvested:
                return
            if qc_stream is not None:
                # 流式模式：harvest 自带有界等待；已随辩论逐条推过 fallacy 事件，
                # 这里只取累计结果注入评委（前端 mergeMarks 对重复标注天然去重）
                qc_harvested = True
                try:
                    ok, outcome = _run_bounded(
                        lambda: qc_stream.harvest(max(0.0, wait)),
                        max(0.0, wait) + ELENCHUS_GUARD_MARGIN,
                    )
                except Exception:  # noqa: BLE001
                    return
                if ok and outcome and outcome[1]:
                    machine_analysis, machine_marks = outcome[0], list(outcome[1])
                    try:
                        ctx.emit("fallacy", marks=machine_marks)
                    except Exception:  # noqa: BLE001 —— 播报失败不影响评审
                        pass
                return
            if not qc_done.wait(timeout=max(0.0, wait)):
                return  # 质检仍在跑：放弃等待，绝不拖住评审
            qc_harvested = True
            if qc_holder["ok"] and qc_holder["outcome"]:
                machine_analysis, machine_marks = qc_holder["outcome"]
                if machine_marks:
                    try:
                        ctx.emit("fallacy", marks=machine_marks)
                    except Exception:  # noqa: BLE001 —— 播报失败不影响评审
                        pass

        if qc_enabled:
            if qc_stream is not None:
                # 流式模式：进度条直接显示"随赛质检"的完成度（如 12 条发言已分析完 10 条）
                emit_progress(stage="质检（随赛并行）")
            else:
                emit_progress(stage="质检")
            threading.Thread(target=qc_worker, name="elenchus-qc", daemon=True).start()

        judge_timeout = float(getattr(ctx.config.judges, "timeout_seconds", 240.0))
        new: list[Speech] = []
        seq = next_seq(base)
        # 自建线程池：asyncio.run 只等待默认线程池；自建池 + shutdown(wait=False) 保证
        # 被遗弃的慢调用绝不拖住节点推进
        executor = ThreadPoolExecutor(max_workers=len(judge_ids) + 1, thread_name_prefix="judges")

        async def one(judge_id: str) -> ScoreCard:
            spec = ctx.agents[judge_id]
            if judge_id == "judge-logic" and qc_enabled:
                # 有界等待质检结论注入（不占事件循环；其余评委不等，照常并行）
                await asyncio.to_thread(collect_qc, QC_JOIN_WAIT)
            messages = build_judge_messages(
                spec=spec, topic=ctx.topic, digest_text=digest_text,
                excerpts=state["digest"].excerpts, checklist=checklist,
                instruction=_judge_instruction(ctx.format),
                machine_analysis_text=machine_analysis if judge_id == "judge-logic" else "",
                conduct_records=conduct_records,
                evidence_sources=evidence_sources,
            )
            card = None
            loop = asyncio.get_running_loop()
            for _ in range(3):  # 首次 + 纠错重试 2 次（带失败原因反馈，SPEC §4.7）
                ctx.check_cancel()
                try:
                    # 线程内执行同步 LLM 调用 → 三位评委真正并发
                    result = await loop.run_in_executor(
                        executor,
                        lambda: ctx.gateway.complete(
                            messages, tier="judge",
                            temperature=ctx.config.model.temperature.judge,
                            max_tokens=1200,
                        ),
                    )
                except LLMExhausted:
                    break
                try:
                    card = parse_scorecard(result.text, judge=judge_id,
                                           focus=JUDGE_FOCUS[judge_id], fmt=ctx.format)
                    break
                except ScoreParseError as exc:
                    # 纠错反馈：失败原因与原输出回传，引导模型修正
                    messages = messages + [
                        {"role": "assistant", "content": result.text},
                        {"role": "user", "content": (
                            f"你的输出不合格式：{exc}。请重新只输出一个 JSON 对象，"
                            "并修正以下常见问题：1) highlights/weaknesses 每侧至少 1 条"
                            "（建议 2~3 条），每条用引号引用具体发言原话并说明理由，"
                            "绝不允许留空或写「无」；2) best_speaker 使用连字符格式"
                            "（如 pro-3 / con-2）；3) 不要输出 JSON 以外的任何文字。"
                        )},
                    ]
            return card if card is not None else abstain_card(judge_id, JUDGE_FOCUS[judge_id], ctx.format)

        async def guarded(judge_id: str) -> tuple[ScoreCard, bool]:
            """单评委评审兜底：总时长超限即按规则弃权——评分环节绝不无限等待。"""
            try:
                return await asyncio.wait_for(one(judge_id), timeout=judge_timeout), False
            except asyncio.TimeoutError:
                # 线程内的调用无法强杀，但本协程立即返回；被遗弃的调用最终自行结束
                return abstain_card(judge_id, JUDGE_FOCUS[judge_id], ctx.format), True

        async def run_all() -> list[ScoreCard]:
            """并行评审 + 完成一位立即播报：评委阶段不再长时间静默。"""
            nonlocal seq
            tasks = [asyncio.create_task(guarded(j)) for j in judge_ids]
            cards: list[ScoreCard] = []
            done = 0
            for fut in asyncio.as_completed(tasks):
                card, timed_out = await fut
                cards.append(card)
                done += 1
                speech = Speech(
                    seq=seq, phase=phase, phase_name=phase_name,
                    speaker=card.judge, camp=Camp.NEUTRAL,
                    text=_judge_summary_text(card), word_count=0,
                    side_notes=["评审超时，按规则弃权"] if timed_out else [],
                )
                _emit(ctx, new, speech)
                seq += 1
                emit_progress(stage="评审", completed=done)
            return cards

        # v2.4：评审期限速覆盖——评委总共只有几次调用，不必沿用辩论期的保守间隔
        # （默认 15s × 3 位评委 = 白白排队 30s+）。0 = 评审期不限速。
        prev_interval = getattr(ctx.gateway, "min_interval", None)
        judge_interval = getattr(ctx.config.judges, "throttle_seconds", None)
        if prev_interval is not None and judge_interval is not None:
            ctx.gateway.min_interval = max(0.0, float(judge_interval))
        try:
            if not qc_enabled:
                emit_progress(stage="评审")  # 质检开启时保留"质检 x/y"阶段展示，直到有评委完成
            cards = asyncio.run(run_all())
        finally:
            heartbeat_stop.set()
            hb_thread.join(timeout=1.0)
            # 超时被遗弃的调用留在池线程中自行结束；shutdown 不等待，评分环节立即推进
            executor.shutdown(wait=False, cancel_futures=True)
            if prev_interval is not None and judge_interval is not None:
                ctx.gateway.min_interval = prev_interval

        # 收尾补收割：评审结束时质检通常早已完成（两者并行）；只再等有限时间，
        # 质检彻底挂死时宁可放弃标注也绝不拖住宣判（有界纪律）
        collect_qc(QC_FINAL_WAIT)
        # v2.6 硬性约束兜底：弃权卡不得留空白点评——用实录与质检标注补出可点评的部分。
        # 放在 collect_qc 之后：谬误标注此时已收割完毕，不足栏有真实素材可引。
        for card in cards:
            if card.abstained:
                _rescue_commentary(card, base, machine_marks)
        emit_progress(stage="完成", completed=len(cards))
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "scorecards": cards, "machine_marks": machine_marks}

    return node
