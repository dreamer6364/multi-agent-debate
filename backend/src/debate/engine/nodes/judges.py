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
        qc_budget = float(getattr(elenchus_cfg, "total_budget_seconds", 90.0))
        QC_JOIN_WAIT = min(  # v2.6 改为可配置：质检是进程内分析，通常秒级完成，20s 白等 rarely 值得
            qc_budget, float(getattr(ctx.config.judges, "qc_join_wait_seconds", 10.0)))
        QC_FINAL_WAIT = float(getattr(ctx.config.judges, "qc_final_wait_seconds", 10.0))
        qc_done = threading.Event()
        qc_holder: dict = {"ok": False, "outcome": None}
        qc_harvested = False
        qc_started = time.monotonic()

        def qc_worker() -> None:
            try:
                ok, outcome = _run_bounded(
                    lambda: elenchus_tool.analyze_speeches_detailed(
                        [s for s in base if s.camp is not Camp.NEUTRAL],
                        per_camp=getattr(elenchus_cfg, "per_camp", 2),
                        check=getattr(elenchus_cfg, "check", "fallacy"),
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
                            "并修正以下常见问题：1) highlights/weaknesses 每侧 2~3 条，"
                            "且每条用引号引用具体发言原话；2) best_speaker 使用连字符格式"
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
        emit_progress(stage="完成", completed=len(cards))
        ctx.emit("phase_end", phase=phase)
        return {"transcript": base + new, "scorecards": cards, "machine_marks": machine_marks}

    return node
