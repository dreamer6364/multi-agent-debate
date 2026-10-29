"""流式金句提取（v2.9）：辩论进行中逐条摘录有价值的句子，实时上屏。

设计（用户需求：「一边生成辩论 一边摘金句」，与 StreamingQC 同一并行纪律）：
  - runner 在开赛时挂一个 GoldenQuoteStream 到 DebateContext.golden；
  - 每条辩手发言定稿（runtime.track_speech）即 submit：丢进线程池让轻量 LLM
    摘金句，完成一条就通过 on_quotes 回调实时推 golden_quote SSE 事件；
  - 引擎终态（runner 返回前）harvest 有界收割剩余任务，随 DebateResult 落库；
  - 全程有界纪律：单条 timeout、线程池 shutdown(wait=False)——金句提取任何挂死
    都不拖辩论/评审，最多损失摘录。

质量纪律：
  - 只摘辩手发言（camp != neutral）且足够长（默认 ≥60 字）；
  - quote 必须是发言原句逐字摘录（LLM 提示词硬约束 + 本地原句校验：非原句丢弃）；
  - 全局去重（本方复读发言会摘出重复金句）、总数封顶（宁缺毋滥）；
  - LLM 通道与 elenchus 相同：直连 OpenAI 兼容免费档（GLM flash），不走 gateway
    的全局限速队列，绝不与辩手生成抢 RPM。
"""
from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import wait as futures_wait
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..schema import Camp, Speech

#: 参与摘录的最短发言（更短的过程性发言难有金句）
MIN_LEN = 60
#: 单条金句摘录上限（提示词同时约束，本地再截断）
DEFAULT_MAX_PER_SPEECH = 2
#: 全场金句总数封顶：宁缺毋滥
DEFAULT_MAX_TOTAL = 16
#: 发言文本注入上限（超长发言只取前段，控制 token 成本）
_MAX_INPUT_CHARS = 2200
#: quote 长度上限（摘录以短句为佳）
_QUOTE_LIMIT = 60

_KINDS = ("逻辑锋利", "修辞出彩", "洞见深刻")

_SYSTEM_PROMPT = (
    "你是辩论赛的金句评审。你的任务是从一条辩手发言中摘出「金句」——真正有价值的句子。"
    "金句的三类来源：逻辑锋利（一针见血地击中对方要害，或完成一次关键论证收束）；"
    "修辞出彩（类比、排比、反问、对照等令人印象深刻的表达）；"
    "洞见深刻（提出新颖视角、精辟概括或重新定义问题）。"
    "硬性要求：\n"
    f"1. quote 必须从发言原文中逐字摘录（可截取一句中最精彩的部分），不超过 {_QUOTE_LIMIT} 字；\n"
    "2. 每条给一句话 comment 说明为什么值得摘录（不超过 30 字）；\n"
    "3. kind 只能取：" + " / ".join(_KINDS) + "；\n"
    f"4. 宁缺毋滥：最多摘 {DEFAULT_MAX_PER_SPEECH} 条；没有够格的金句就返回空数组，绝不为凑数硬摘平庸句子；\n"
    "5. 只输出一个 JSON 对象，格式："
    '{"quotes": [{"quote": "...", "comment": "...", "kind": "..."}]}，不要输出任何其他文字。'
)

_STRIP = re.compile(r"[\s，。、；：？！\"'“”‘’（）()《》<>【】\[\]·…—\-,.:;?!]+")


def _normalize(text: str) -> str:
    """金句去重键：去掉所有空白与标点后的小写文本（复读发言/微调措辞视为同句）。"""
    return _STRIP.sub("", text or "").lower()


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出里抠出第一个 JSON 对象（容忍代码围栏与前后闲话）。"""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _chat(text: str, timeout: float) -> str | None:
    """调用 LLM 返回原始输出文本；网络/接口任何失败抛异常（由调用方兜住）。"""
    from openai import OpenAI

    extra = None
    raw = os.environ.get("LLM_EXTRA_BODY_JSON")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                extra = parsed
        except json.JSONDecodeError:
            extra = None
    client = OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        api_key=os.environ["OPENAI_API_KEY"],
        timeout=timeout,
    )
    prompt = f"【辩手发言原文】\n{text[:_MAX_INPUT_CHARS]}"
    resp = client.chat.completions.create(
        model=os.environ.get("MODEL_OFFICIAL", "glm-4.5-flash"),
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=600,
        extra_body=extra,
    )
    return resp.choices[0].message.content or ""


def extract_quotes(text: str, *, timeout: float = 30.0, max_quotes: int = DEFAULT_MAX_PER_SPEECH) -> list[dict]:
    """单条发言的金句摘录（阻塞调用，供线程池使用）。

    返回 [{quote, comment, kind}]；LLM 未配置 / 超时 / 输出不合格 一律返回 []（静默降级）。
    本地原句校验：quote 归一化后必须包含于发言原文，改写/编造句直接丢弃。
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return []
    try:
        raw = _chat(text, timeout)
        data = _extract_json(raw) if raw else None
    except Exception:  # noqa: BLE001 —— 金句提取任何失败都静默降级
        return []
    norm_text = _normalize(text)
    quotes: list[dict] = []
    for item in (data or {}).get("quotes") or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()[:_QUOTE_LIMIT]
        key = _normalize(quote)
        if not key or key not in norm_text:  # 非原句（改写/幻觉）丢弃
            continue
        kind = str(item.get("kind") or "洞见深刻")
        if kind not in _KINDS:
            kind = "洞见深刻"
        quotes.append({
            "quote": quote,
            "comment": str(item.get("comment") or "").strip()[:60],
            "kind": kind,
        })
    return quotes[:max_quotes]


class GoldenQuoteStream:
    """逐条流式金句摘录。线程安全：submit 来自引擎线程，harvest 来自 runner 收尾。"""

    def __init__(self, *, timeout: float = 30.0, parallel: int = 2,
                 max_per_speech: int = DEFAULT_MAX_PER_SPEECH,
                 max_total: int = DEFAULT_MAX_TOTAL,
                 min_len: int = MIN_LEN,
                 on_quotes: Callable[[list[dict]], None] | None = None) -> None:
        self.timeout = timeout
        self.max_per_speech = max(1, int(max_per_speech))
        self.max_total = max(1, int(max_total))
        self.min_len = min_len
        self.on_quotes = on_quotes
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(parallel)),
                                        thread_name_prefix="golden-stream")
        self._lock = threading.Lock()
        self._futures: dict[Any, Speech] = {}   # future → speech（未收割的）
        self._quotes: list[dict] = []           # 累计金句（seq 升序展示）
        self._seen: set[str] = set()            # 去重键
        self._submitted: set[int] = set()       # speech seq 去重
        self._closed = False

    # ---------- 对外：发言落定即提交 ----------

    def submit(self, speech: Speech) -> None:
        """每条辩手发言定稿后调用：入池异步摘录（neutral/过短/重复提交直接跳过）。"""
        if self._closed or speech.camp is Camp.NEUTRAL or len(speech.text) < self.min_len:
            return
        with self._lock:
            if speech.seq in self._submitted:
                return
            self._submitted.add(speech.seq)

        def worker() -> list[dict]:
            return extract_quotes(speech.text, timeout=self.timeout,
                                  max_quotes=self.max_per_speech)

        fut = self._pool.submit(worker)
        with self._lock:
            self._futures[fut] = speech

    # ---------- 内部：结果归并 ----------

    def _drain_completed_locked(self) -> int:
        """收割所有已完成的 future；返回新完成条数（调用方持有锁）。"""
        if not self._futures:
            return 0
        done, _ = futures_wait(list(self._futures), timeout=0)
        fresh = 0
        for fut in done:
            speech = self._futures.pop(fut)
            try:
                found = fut.result()
            except Exception:  # noqa: BLE001 —— 单条失败（含 ImportError 降级）跳过
                continue
            fresh += 1
            for item in found:
                key = _normalize(item.get("quote", ""))
                if not key or key in self._seen or len(self._quotes) >= self.max_total:
                    continue
                self._seen.add(key)
                self._quotes.append({
                    "seq": speech.seq,
                    "camp": speech.camp.value,
                    "speaker": speech.speaker,
                    "phase_name": speech.phase_name,
                    "quote": item.get("quote", ""),
                    "comment": item.get("comment", ""),
                    "kind": item.get("kind", "洞见深刻"),
                })
        return fresh

    def _emit_new_locked(self, before: int) -> None:
        """把本次新增的金句推给回调（SSE 实时上屏）。"""
        if self.on_quotes is None or len(self._quotes) <= before:
            return
        try:
            self.on_quotes(list(self._quotes[before:]))
        except Exception:  # noqa: BLE001 —— 播报失败不影响摘录
            pass

    # ---------- 对外：终态有界收割 ----------

    def harvest(self, wait: float = 0.0) -> list[dict]:
        """有界等待剩余任务并返回累计金句（按发言 seq 升序）。幂等可多次调用。"""
        import time as _time

        deadline = _time.monotonic() + max(0.0, wait)
        while True:
            with self._lock:
                before = len(self._quotes)
                self._drain_completed_locked()
                self._emit_new_locked(before)
                pending = len(self._futures)
            if pending == 0:
                break
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                break  # 等待预算耗尽：未完成的留给下次 harvest / close 遗弃
            _time.sleep(min(0.5, remaining))
        with self._lock:
            return sorted(self._quotes, key=lambda q: q["seq"])

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._futures)

    def close(self) -> None:
        """引擎终态调用：不再接受新任务；卡住的线程遗弃（shutdown 不等待）。"""
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._futures.clear()  # 遗弃的任务不再计入 pending
