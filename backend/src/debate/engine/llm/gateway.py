"""LLM 网关（SPEC §7 / ARCH §8）：OpenAI 兼容协议，重试→耗尽抛 LLMExhausted。

backend 注入点：传入 tests.fakes.FakeLLM 即为测试路径（FakeLLM.complete 签名兼容）；
backend=None 时按 .env 懒加载 openai 客户端（M2 冒烟起用）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMExhausted(RuntimeError):
    """重试次数耗尽，由引擎节点落占位降级（SPEC §4.9）。"""


# ---- 文本型工具调用（部分 OpenAI 兼容后端不回原生 tool_calls，而是把调用以伪 XML 写进正文） ----

#: <tool_call>...</tool_call> 整块（未闭合也算，兜底剥掉）
_TOOL_BLOCK_RE = re.compile(r"<tool_call>.*?(?:</tool_call>|$)", re.S)
#: <arg_key>k</arg_key><arg_value>v</arg_value> 参数对
_ARG_PAIR_RE = re.compile(r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>", re.S)
#: 调用块里的 JSON 对象（Qwen/GLM 风格 {"name": ..., "arguments": {...}}）
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)
#: 悬空的工具名行（单独一行词，后面紧跟标签）
_BARE_NAME_RE = re.compile(r"^\s*[A-Za-z_]\w*\s*\n(?=<)", re.M)


def parse_text_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]] | None:
    """从正文解析文本型工具调用；返回 None 表示正文没有调用痕迹。

    兼容两种形态：
      1. ``<tool_call>{"name": "x", "arguments": {...}}</tool_call>``（JSON 风格）
      2. ``search_evidence`` + ``<arg_key>query</arg_key><arg_value>...</arg_value>``（伪 XML 参数对）
    有痕迹但解析不出参数时返回 []（调用方至少要把痕迹从正文剥掉）。
    """
    if "<tool_call" not in text and "<arg_key>" not in text:
        return None
    blocks = _TOOL_BLOCK_RE.findall(text) or [text]
    calls: list[tuple[str, dict[str, Any]]] = []
    for blob in blocks:
        obj: Any = None
        m = _JSON_OBJ_RE.search(blob)
        if m:
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                obj = None
        if isinstance(obj, dict) and obj.get("name"):
            args = obj.get("arguments") or obj.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append((str(obj["name"]), args if isinstance(args, dict) else {}))
            continue
        head = blob.split("<arg_key>", 1)[0]
        head = re.sub(r"</?[A-Za-z_]\w*>", "", head)  # 剥掉 <tool_call> 之类残留标签再取名字
        name = re.split(r"[\s:：]+", head.strip())[0] if head.strip() else ""
        pairs = _ARG_PAIR_RE.findall(blob)
        if name and pairs:
            calls.append((name, {k.strip(): v.strip() for k, v in pairs}))
    return calls


def strip_tool_artifacts(text: str) -> str:
    """把正文里的工具调用痕迹剥干净（兜底：不执行也要保证发言不泄漏调用协议）。"""
    t = _TOOL_BLOCK_RE.sub("", text)
    t = _ARG_PAIR_RE.sub("", t)
    t = re.sub(r"</?tool_call>", "", t)
    if t == text and "<arg_" not in text:  # 没有任何调用痕迹：原样返回，不做激进清理
        return text.strip()
    t = _BARE_NAME_RE.sub("", t)
    t = t.strip()
    if re.fullmatch(r"[A-Za-z_]\w*", t):  # 参数剥掉后只剩孤零零一个工具名 → 整条都是调用
        return ""
    return t


class _StreamToolFilter:
    """流式增量过滤器：压制混进 speech_delta 的工具调用痕迹。

    策略：安全前缀即时放行；一旦出现完整工具标签（``<tool_call>``/``<arg_key>``…），
    吞掉整个区域直到对应闭合标签；尾部可能是标签截断时扣留待定。
    """

    _OPENS = ("<tool_call>", "<arg_key>", "<arg_value>")
    _CLOSES = ("</tool_call>", "</arg_key>", "</arg_value>")
    _ALL = _OPENS + _CLOSES
    _STUCK_LIMIT = 4000  # arg 区域迟迟不闭合的兜底上限，防止无限吞字

    def __init__(self) -> None:
        self._buf = ""

    def _partial_tail(self) -> int:
        """缓冲区尾部可能是某个标签前缀的长度（0 = 无截断标签，可全放行）。"""
        for n in range(min(len(self._buf), 12), 0, -1):
            tail = self._buf[-n:]
            if any(t.startswith(tail) for t in self._ALL):
                return n
        return 0

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while True:
            hits = [(self._buf.find(t), t) for t in self._ALL if t in self._buf]
            if not hits:
                keep = self._partial_tail()
                emit = self._buf[: len(self._buf) - keep] if keep else self._buf
                out.append(emit)
                self._buf = self._buf[len(emit):]
                return "".join(out)
            idx, tok = min(hits, key=lambda x: x[0])
            if idx > 0:
                out.append(self._buf[:idx])
                self._buf = self._buf[idx:]
            if tok in self._CLOSES:  # 悬空闭合标签：直接丢弃
                self._buf = self._buf[len(tok):]
                continue
            end = "</tool_call>" if tok == "<tool_call>" else "</arg_value>"
            end_idx = self._buf.find(end, len(tok))
            if end_idx == -1:
                if len(self._buf) > self._STUCK_LIMIT:  # 区域不闭合：整体按噪声丢弃，防吞字
                    self._buf = ""
                return "".join(out)
            self._buf = self._buf[end_idx + len(end):]

    def flush(self) -> str:
        """流结束：扣留的尾巴若仍是标签片段则丢弃，其余放行。"""
        keep = self._partial_tail()
        tail = self._buf[: len(self._buf) - keep] if keep else self._buf
        self._buf = ""
        return tail


def extra_body_from_env() -> dict[str, Any] | None:
    """LLM_EXTRA_BODY_JSON 环境变量 → OpenAI 兼容透传参数（如 GLM thinking: disabled）。"""
    raw = os.environ.get("LLM_EXTRA_BODY_JSON")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class Gateway:
    backend: Any | None = None  # complete(messages, **kw) -> str | LLMResult
    model_debater: str = "gpt-model"
    model_official: str = "gpt-model-lite"
    max_retries: int = 3
    min_interval: float = 0.0  # 全局限速：相邻调用最小间隔秒（免费档 RPM 受限时配置，如 12）
    sleep: Callable[[float], None] = time.sleep
    _last_call: float = field(default_factory=lambda: 0.0)
    _throttle_lock: threading.Lock = field(default_factory=threading.Lock)  # v1.9.1：评委并发后多线程调用
    extra_body: dict[str, Any] | None = None  # OpenAI 兼容透传（如 GLM thinking: disabled）
    usage: dict[str, int] = field(default_factory=lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    _client: Any = None

    def _throttle(self) -> None:
        """全局限速（v1.4）：距上次调用不足 min_interval 则等待——免费档 RPM 保护。

        v1.9.1 起加锁：评委并行（多线程）调用时保证间隔计算与时间戳更新原子。
        """
        if self.min_interval <= 0:
            return
        with self._throttle_lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_call)
            if wait > 0:
                self.sleep(wait)
            self._last_call = time.monotonic()

    def complete(
        self, messages: list[dict[str, str]], *, tier: str, temperature: float, max_tokens: int
    ) -> LLMResult:
        """tier ∈ debater/judge/chair/digest：辩手走主力模型，其余走廉价模型（ARCH D6）。"""
        self._throttle()
        model = self.model_debater if tier == "debater" else self.model_official
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                if self.backend is not None:
                    raw = self.backend.complete(messages, model=model, temperature=temperature, max_tokens=max_tokens)
                else:
                    raw = self._call_openai(messages, model, temperature, max_tokens)
                result = raw if isinstance(raw, LLMResult) else LLMResult(text=str(raw))
                cleaned = strip_tool_artifacts(result.text)
                if cleaned != result.text:  # 兜底：正文混进文本型工具调用痕迹时剥掉
                    result = LLMResult(text=cleaned, prompt_tokens=result.prompt_tokens,
                                       completion_tokens=result.completion_tokens)
                self.usage["calls"] += 1
                self.usage["prompt_tokens"] += result.prompt_tokens
                self.usage["completion_tokens"] += result.completion_tokens
                return result
            except Exception as exc:  # noqa: BLE001 —— 统一重试，最终以 LLMExhausted 出口
                last_exc = exc
                if attempt < self.max_retries:
                    self.sleep(self._backoff(attempt, exc))
        raise LLMExhausted(f"LLM 重试 {self.max_retries} 次后仍失败: {last_exc}") from last_exc

    @staticmethod
    def _backoff(attempt: int, exc: Exception) -> float:
        """限流（429/RateLimit*）用长退避扛过按分钟计的限窗；其余指数退避。"""
        name = type(exc).__name__
        if "RateLimit" in name or "429" in str(exc):
            return 90.0  # 免费档限窗较长的惩罚期：90s 退避
        return float(min(2**attempt, 8))

    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str,
        temperature: float,
        max_tokens: int,
        on_attempt: Callable[[int], None] | None = None,
    ):
        """流式调用（v2.1 speech_delta 链路）：逐 chunk yield 文本片段。

        重试语义与 complete 相同；每次尝试开始前经 on_attempt(attempt) 通知调用方，
        调用方据此向下游发"重置"信号（attempt 变化 → 前端清空该 seq 的累积文本）。
        usage 以估算口径统计（prompt 按字符估、completion 按累计字数）。
        """
        self._throttle()
        model = self.model_debater if tier == "debater" else self.model_official
        last_exc: Exception | None = None
        flt = _StreamToolFilter()  # 压制混进正文的文本型工具调用（防 speech_delta 泄漏）
        for attempt in range(self.max_retries + 1):
            try:
                if on_attempt is not None:
                    on_attempt(attempt)
                collected: list[str] = []
                if self.backend is not None and hasattr(self.backend, "stream"):
                    chunk_iter = self.backend.stream(messages, model=model, temperature=temperature, max_tokens=max_tokens)
                elif self.backend is not None:
                    text = str(self.backend.complete(messages, model=model, temperature=temperature, max_tokens=max_tokens))
                    chunk_iter = (text[i : i + 3] for i in range(0, len(text), 3))
                else:
                    chunk_iter = self._stream_openai(messages, model, temperature, max_tokens)
                for chunk in chunk_iter:
                    if not chunk:
                        continue
                    cleaned = flt.feed(chunk)
                    if cleaned:
                        collected.append(cleaned)
                        yield cleaned
                tail = flt.flush()
                if tail:
                    collected.append(tail)
                    yield tail
                self.usage["calls"] += 1
                self.usage["prompt_tokens"] += sum(len(m.get("content", "")) for m in messages)
                self.usage["completion_tokens"] += sum(len(c) for c in collected)
                return
            except Exception as exc:  # noqa: BLE001 —— 与 complete 相同的重试出口
                last_exc = exc
                if attempt < self.max_retries:
                    self.sleep(self._backoff(attempt, exc))
        raise LLMExhausted(f"LLM 流式重试 {self.max_retries} 次后仍失败: {last_exc}") from last_exc

    def _stream_openai(
        self, messages: list[dict[str, str]], model: str, temperature: float, max_tokens: int
    ):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                base_url=os.environ.get("OPENAI_BASE_URL") or None,
                timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "120")),
            )
        kwargs: dict[str, Any] = {"stream": True}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        response = self._client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, **kwargs
        )
        for chunk in response:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield content

    def _call_openai(
        self, messages: list[dict[str, str]], model: str, temperature: float, max_tokens: int
    ) -> LLMResult:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                base_url=os.environ.get("OPENAI_BASE_URL") or None,
                timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "120")),
            )
        kwargs: dict[str, Any] = {}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        response = self._client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, **kwargs
        )
        usage = getattr(response, "usage", None)
        return LLMResult(
            text=response.choices[0].message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    # ---- v1.8 Agent 工具循环（function calling）----

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        execute: Callable[[str, dict[str, Any]], str],
        tier: str,
        temperature: float,
        max_tokens: int,
        max_rounds: int = 2,
    ) -> LLMResult:
        """带工具循环的对话：模型返回 tool_calls 则执行并把结果回注，直至给出正文。

        - backend 实现了 chat_with_tools（测试替身/异构后端）→ 直接委托；
        - backend 存在但未实现（FakeLLM 等）→ 忽略工具走 complete（兜底兼容）；
        - 否则走 OpenAI 兼容 function calling 原生循环。
        任何异常由调用方捕获降级为普通 complete。
        """
        if self.backend is not None:
            if hasattr(self.backend, "chat_with_tools"):
                result = self.backend.chat_with_tools(
                    messages, tools=tools, execute=execute, tier=tier,
                    temperature=temperature, max_tokens=max_tokens, max_rounds=max_rounds,
                )
                self.usage["calls"] += 1
                self.usage["prompt_tokens"] += result.prompt_tokens
                self.usage["completion_tokens"] += result.completion_tokens
                cleaned = strip_tool_artifacts(result.text)
                if cleaned != result.text:  # 异构后端同样可能把调用写进正文
                    result = LLMResult(text=cleaned, prompt_tokens=result.prompt_tokens,
                                       completion_tokens=result.completion_tokens)
                return result
            return self.complete(messages, tier=tier, temperature=temperature, max_tokens=max_tokens)

        self._throttle()
        model = self.model_debater if tier == "debater" else self.model_official
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY", ""),
                base_url=os.environ.get("OPENAI_BASE_URL") or None,
                timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "120")),
            )
        convo = [dict(m) for m in messages]
        prompt_tokens = 0
        completion_tokens = 0
        kwargs: dict[str, Any] = {"tools": tools, "tool_choice": "auto"}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        for round_index in range(max_rounds + 1):
            response = self._client.chat.completions.create(
                model=model, messages=convo, temperature=temperature, max_tokens=max_tokens, **kwargs
            )
            usage = getattr(response, "usage", None)
            prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None)
            if not calls:
                text = message.content or ""
                parsed = parse_text_tool_calls(text)
                if parsed is None:
                    self.usage["calls"] += 1
                    self.usage["prompt_tokens"] += prompt_tokens
                    self.usage["completion_tokens"] += completion_tokens
                    return LLMResult(text=text, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
                cleaned = strip_tool_artifacts(text)
                if parsed and round_index < max_rounds:
                    # 文本型工具调用：后端没走原生协议但语义等价——执行后回注，继续循环
                    convo.append({
                        "role": "assistant",
                        "content": cleaned,
                        "tool_calls": [
                            {
                                "id": f"text_{round_index}_{i}",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                            }
                            for i, (name, args) in enumerate(parsed)
                        ],
                    })
                    for i, (name, args) in enumerate(parsed):
                        try:
                            result_text = execute(name, args)
                        except Exception as exc:  # noqa: BLE001 —— 工具失败回注错误，模型自行调整
                            result_text = f"工具执行失败：{exc}"
                        convo.append({"role": "tool", "tool_call_id": f"text_{round_index}_{i}", "content": result_text})
                    continue
                if cleaned:  # 轮数耗尽/解析失败：至少把调用痕迹从正文剥掉
                    self.usage["calls"] += 1
                    self.usage["prompt_tokens"] += prompt_tokens
                    self.usage["completion_tokens"] += completion_tokens
                    return LLMResult(text=cleaned, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
                # 整条都是无法解析的调用痕迹 → 跳出走无工具收束
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                continue
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                    }
                    for tc in calls
                ],
            }
            convo.append(assistant_msg)
            for tc in calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}
                try:
                    result_text = execute(tc.function.name, args)
                except Exception as exc:  # noqa: BLE001 —— 工具失败回注错误，模型自行调整
                    result_text = f"工具执行失败：{exc}"
                convo.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
        # 轮数耗尽：最后再强制一次无工具收束
        kwargs.pop("tools", None)
        kwargs.pop("tool_choice", None)
        response = self._client.chat.completions.create(
            model=model, messages=convo, temperature=temperature, max_tokens=max_tokens, **kwargs
        )
        usage = getattr(response, "usage", None)
        text = strip_tool_artifacts(response.choices[0].message.content or "")
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += prompt_tokens + (getattr(usage, "prompt_tokens", 0) or 0)
        self.usage["completion_tokens"] += completion_tokens + (getattr(usage, "completion_tokens", 0) or 0)
        return LLMResult(text=text, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
