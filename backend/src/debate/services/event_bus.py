"""进程内事件总线（ARCH A1，单实例假设；水平扩展时以 Redis pub/sub 替换本文件）。

每个进行中的比赛一个总线。speech 事件 id = 发言 seq（断线续传锚点）；
其余事件 id 为总线内单调自增，且整体落在独立高位段（ISS-012 BE-3）——
两类 id 不再数值重叠，未来按 Last-Event-ID 恢复时不会把 speech 序号与非 speech
序号混为一谈（SPEC §6.1/§6.2）。只在事件循环线程调用 publish。
"""
from __future__ import annotations

import asyncio

NON_SPEECH_ID_BASE = 10_000_000  # 远大于单场发言数上限；speech id 恒 < 此值


class EventBus:
    def __init__(self, debate_id: int):
        self.debate_id = debate_id
        self._subscribers: set[asyncio.Queue] = set()
        self._non_speech_id = 0
        self.last_phase_start: dict | None = None  # 重连快照用（SPEC §6.1）
        self.last_swimlane: dict | None = None  # v2.7 重连快照用：补当前时刻的复盘泳道总结
        self.golden_quotes: list[dict] = []  # v2.9 重连快照用：累计金句（增量事件 → 重连全量补发）
        self.fallacy_marks: list[dict] = []  # v2.9.4 重连快照用（BE-P2-2）：累计谬误标注，与金句对称
        self.closed = False

    def allocate_id(self) -> int:
        self._non_speech_id += 1
        return NON_SPEECH_ID_BASE + self._non_speech_id

    def publish(self, event: dict) -> None:
        kind = event.get("event")
        if kind != "speech":
            event = {**event, "id": self.allocate_id()}
            if kind == "phase_start":
                self.last_phase_start = event["data"]
            elif kind == "swimlane":
                self.last_swimlane = event["data"]
            elif kind == "golden_quote":
                self.golden_quotes.extend(event["data"].get("quotes", []))
            elif kind == "fallacy":
                self.fallacy_marks.extend(event["data"].get("marks", []))
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def close(self) -> None:
        self.closed = True
