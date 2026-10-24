"""FakeLLM（TEST_PLAN §2.1）：与 llm/gateway 相同调用签名的测试替身。

三模式：
- scripted: 按剧本 fixture 顺序回放预置发言，耗尽抛 ScriptExhausted；
- echo:     识别 "{repeat:N}" 占位符展开 N 字（测计时/截断边界）；
- fault:    故障注入（timeout / http_500 / malformed_json / oversized /
            abuse / interrupt_spam / meta）。
真模型只出现在 E2E 冒烟，一切含 LLM 的测试路径用本替身。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

META_TEXT = "作为AI我将客观分析这道辩题。"
ABUSE_TEXT = "对方辩友简直是诡辩的废物。"
INTERRUPT_SPAM_TEXT = "[打断]一[打断]二[打断]三后续问题。"
OVERSIZED_TEXT_TEMPLATE = "{repeat:3000}"


class FakeLLMError(RuntimeError):
    """模拟的 LLM 调用失败。"""


class ScriptExhausted(FakeLLMError):
    """剧本回放完毕。"""


class FakeLLM:
    @classmethod
    def scripted(cls, script_path: str | Path) -> "FakeLLM":
        data = json.loads(Path(script_path).read_text(encoding="utf-8"))
        return cls(mode="scripted", script=[turn["text"] for turn in data.get("turns", [])])

    def __init__(
        self,
        mode: str = "scripted",
        script: list[str] | None = None,
        error_kind: str | None = None,
        stream_chunk: int = 3,
        stream_delay: float = 0.0,
    ):
        if mode not in ("scripted", "echo", "fault"):
            raise ValueError(f"未知模式: {mode}")
        self.mode = mode
        self._script = list(script or [])
        self.error_kind = error_kind
        self.calls: list[list[dict[str, Any]]] = []
        self.stream_chunk = stream_chunk  # 流式切片粒度（字/片）
        self.stream_delay = stream_delay  # 切片间隔（演示打字机效果用）

    def _next_response(self, messages: list[dict[str, Any]]) -> str:
        if self.mode == "fault":
            return self._fault()
        if self.mode == "echo":
            return self._echo(messages)
        if not self._script:
            raise ScriptExhausted("剧本已耗尽")
        return self._script.pop(0)

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        self.calls.append(messages)
        return self._next_response(messages)

    def stream(self, messages: list[dict[str, Any]], **kwargs: Any):
        """流式替身：整文先取，再按 stream_chunk 切片 yield。"""
        import time as _time

        self.calls.append(messages)
        text = self._next_response(messages)
        for i in range(0, len(text), self.stream_chunk):
            yield text[i : i + self.stream_chunk]
            if self.stream_delay > 0:
                _time.sleep(self.stream_delay)

    def _echo(self, messages: list[dict[str, Any]]) -> str:
        import re

        prompt = messages[-1]["content"] if messages else ""
        match = re.search(r"\{repeat:(\d+)\}", prompt)
        if match:
            return "字" * int(match.group(1))
        return "回声：" + prompt[-50:]

    def _fault(self) -> str:
        kind = self.error_kind or "http_500"
        if kind == "timeout":
            raise FakeLLMError("模拟调用超时")
        if kind == "http_500":
            raise FakeLLMError("模拟服务端 500")
        if kind == "malformed_json":
            return "这不是一个 JSON 对象"
        if kind == "oversized":
            return "字" * 3000
        if kind == "abuse":
            return ABUSE_TEXT
        if kind == "interrupt_spam":
            return INTERRUPT_SPAM_TEXT
        if kind == "meta":
            return META_TEXT
        raise ValueError(f"未知故障类型: {kind}")
