"""LLM 网关（SPEC §7 / ARCH §8）：OpenAI 兼容协议，重试→耗尽抛 LLMExhausted。

backend 注入点：传入 tests.fakes.FakeLLM 即为测试路径（FakeLLM.complete 签名兼容）；
backend=None 时按 .env 懒加载 openai 客户端（M2 冒烟起用）。
"""
from __future__ import annotations

import json
import os
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
    extra_body: dict[str, Any] | None = None  # OpenAI 兼容透传（如 GLM thinking: disabled）
    usage: dict[str, int] = field(default_factory=lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    _client: Any = None

    def _throttle(self) -> None:
        """全局限速（v1.4）：距上次调用不足 min_interval 则等待——免费档 RPM 保护。"""
        if self.min_interval <= 0:
            return
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
            return 45.0
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
                    collected.append(chunk)
                    yield chunk
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
