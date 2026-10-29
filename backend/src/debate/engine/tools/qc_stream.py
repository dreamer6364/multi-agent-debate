"""流式质检（v2.8.1）：辩论进行中逐条分析发言，而不是等比赛结束后集中跑。

设计（用户需求：「一边生成辩论 一边质检」）：
  - runner 在开赛时挂一个 StreamingQC 到 DebateContext.qc；
  - 每条辩手发言定稿（runtime.track_speech）即 submit：丢进线程池做 Elenchus 分析，
    完成一条就通过 on_marks 回调实时推 fallacy SSE 事件（前端 mergeMarks 去重合并）；
  - 评审节点不再从零起跑批量质检，只 harvest：有界等待剩余任务收尾（通常秒级）；
  - 全程有界纪律：单条 timeout、线程池 shutdown(wait=False)——质检任何挂死都
    不拖辩论/评审，最多损失标注。

严格性增强：
  - 覆盖面：从「每方最长的 per_camp 条」扩大到**全部辩手发言**（≥40 字）；
  - 确定性规则：本方复读检测（8-gram 重叠率）零成本即时判定，置信「高」；
  - sensitivity 沿用 strict（低置信的细微问题也报）。
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import wait as futures_wait
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..schema import CAMP_CN, Camp, Speech

#: 复读检测参数：8 字滑窗，重叠率（相对较短文本）≥ 0.5 判为复读
_REPEAT_NGRAM = 8
_REPEAT_RATIO = 0.5
#: 只对足够长的发言做复读检测（短句撞窗是巧合不是复读）
_REPEAT_MIN_LEN = 80

_WHITESPACE = re.compile(r"\s+")


def _ngrams(text: str, n: int = _REPEAT_NGRAM) -> set[str]:
    t = _WHITESPACE.sub("", text)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def repetition_ratio(text: str, previous: str) -> float:
    """两段文本的 8-gram 重叠率（归一到较短一方；任一方过短返回 0）。"""
    ga, gb = _ngrams(text), _ngrams(previous)
    denom = min(len(ga), len(gb))
    if denom == 0:
        return 0.0
    return len(ga & gb) / denom


class StreamingQC:
    """逐条流式谬误检测。线程安全：submit 来自引擎线程，harvest 来自评审线程。"""

    def __init__(self, *, check: str = "fallacy", sensitivity: str = "strict",
                 timeout: float = 45.0, parallel: int = 4,
                 on_marks: Callable[[list[dict]], None] | None = None,
                 on_progress: Callable[[int, int], None] | None = None) -> None:
        self.check = check
        self.sensitivity = sensitivity
        self.timeout = timeout
        self.on_marks = on_marks
        self.on_progress = on_progress
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(parallel)),
                                        thread_name_prefix="qc-stream")
        self._lock = threading.Lock()
        self._futures: dict[Any, Speech] = {}  # future → speech（未收割的）
        self._lines: list[str] = []            # 注入给逻辑评委的分析行
        self._marks: list[dict] = []           # 结构化标注（含确定性规则）
        self._submitted: set[int] = set()      # seq 去重
        self._own_history: list[tuple[int, str]] = []  # (seq, text)：本方复读检测用
        self._total = 0                        # 已提交总数（进度分母）
        self._closed = False

    # ---------- 对外：发言落定即提交 ----------

    def submit(self, speech: Speech) -> None:
        """每条辩手发言定稿后调用：确定性规则即时判定，LLM 分析入池异步跑。"""
        if self._closed or speech.camp is Camp.NEUTRAL or len(speech.text) < 40:
            return
        with self._lock:
            if speech.seq in self._submitted:
                return
            self._submitted.add(speech.seq)
            # 确定性复读检测：与本方此前发言比对，零成本、即时出标注
            for prev_seq, prev_text in self._own_history:
                if len(prev_text) < _REPEAT_MIN_LEN or len(speech.text) < _REPEAT_MIN_LEN:
                    continue
                ratio = repetition_ratio(speech.text, prev_text)
                if ratio >= _REPEAT_RATIO:
                    self._add_mark_locked(speech, name="复读论据",
                                          quote=speech.text[:60],
                                          explanation=f"与本方第 {prev_seq} 条发言高度重复"
                                                      f"（相似度 {ratio:.0%}），缺乏新论证",
                                          confidence="高（确定性规则）")
                    break
            self._own_history.append((speech.seq, speech.text))
            self._total += 1

        from .elenchus import analyze_one  # 调用时取：单测可 monkeypatch

        def worker() -> dict:
            return analyze_one(speech.text, check=self.check, timeout=self.timeout,
                               mode="inproc", sensitivity=self.sensitivity)

        fut = self._pool.submit(worker)
        with self._lock:
            self._futures[fut] = speech

    # ---------- 内部：结果归并 ----------

    def _add_mark_locked(self, speech: Speech, *, name: str, quote: str,
                         explanation: str, confidence: str) -> None:
        self._lines.append(
            f"- [{CAMP_CN[speech.camp]}·{speech.speaker}] {name}（{confidence}）：引用“{quote}”")
        self._marks.append({
            "seq": speech.seq, "camp": speech.camp.value, "speaker": speech.speaker,
            "name": name, "quote": quote, "explanation": explanation[:160],
            "confidence": confidence,
        })

    def _collect_findings_locked(self, speech: Speech, result: dict) -> None:
        """LLM 分析结果归并（strict 单条最多 7 条，与批量模式一致）。"""
        for f in (result.get("findings") or [])[:7]:
            name = f.get("name", "?")
            quote = (f.get("quote") or "")[:60]
            conf = f.get("confidence", "?")
            self._add_mark_locked(speech, name=name, quote=quote,
                                  explanation=(f.get("explanation") or "")[:160],
                                  confidence=conf)

    def _drain_completed_locked(self) -> int:
        """收割所有已完成的 future；返回新完成条数（调用方持有锁）。"""
        if not self._futures:
            return 0
        done, _ = futures_wait(list(self._futures), timeout=0)
        fresh = 0
        for fut in done:
            speech = self._futures.pop(fut)
            try:
                result = fut.result()
            except Exception:  # noqa: BLE001 —— 单条失败（含 ImportError 降级）跳过
                continue
            fresh += 1
            self._collect_findings_locked(speech, result)
        return fresh

    def _notify_progress_locked(self) -> None:
        if self.on_progress is None:
            return
        done = self._total - len(self._futures)
        try:
            self.on_progress(done, self._total)
        except Exception:  # noqa: BLE001
            pass

    def _emit_new_marks_locked(self, before: int) -> None:
        """把本次新增的结构化标注推给回调（SSE 实时挂到发言卡）。"""
        if self.on_marks is None or len(self._marks) <= before:
            return
        try:
            self.on_marks(list(self._marks[before:]))
        except Exception:  # noqa: BLE001 —— 播报失败不影响分析
            pass

    # ---------- 对外：评审阶段有界收割 ----------

    def harvest(self, wait: float = 0.0) -> tuple[str, list[dict]]:
        """有界等待剩余任务并返回累计结果 (注入段文本, 全部标注)。幂等可多次调用。"""
        import time as _time

        deadline = _time.monotonic() + max(0.0, wait)
        while True:
            with self._lock:
                before = len(self._marks)
                self._drain_completed_locked()
                self._emit_new_marks_locked(before)
                pending = len(self._futures)
                self._notify_progress_locked()
            if pending == 0:
                break
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                break  # 等待预算耗尽：未完成的留给下次 harvest / close 遗弃
            _time.sleep(min(0.5, remaining))
        with self._lock:
            if not self._lines:
                return "", []
            text = ("【机器论证分析】（Elenchus 工具检测，仅供参考——每条须你自行核验引用与结论后才可采信）\n"
                    + "\n".join(self._lines))
            return text, list(self._marks)

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._futures)

    def close(self) -> None:
        """比赛终态调用：不再接受新任务；卡住的线程遗弃（shutdown 不等待）。"""
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._futures.clear()  # 遗弃的任务不再计入 pending
