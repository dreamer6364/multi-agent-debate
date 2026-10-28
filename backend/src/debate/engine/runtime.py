"""引擎运行时上下文与流程级异常（ARCH §4.2 DebateState 之外的"闭包依赖"）。

依赖（config/topic/agents/gateway/回调）走闭包注入而非图状态，
保证图状态可序列化、节点纯净（CONSTRAINTS B2：引擎不感知 Web）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .llm.gateway import Gateway
from .rag.corpus import EvidenceCard
from .schema import Camp, DebateFormat, Speech, Topic
from .agents.loader import AgentSpec
from ..core.config import AppConfig

if TYPE_CHECKING:  # 仅类型标注：graph 包会反向依赖 schema/agents，避免运行时循环导入
    from .graph.debate_state import GraphTracker


class DebateCancelled(Exception):
    """协作式取消（stop）：在节点边界退出，已产出发言保留。"""


class DebateAborted(Exception):
    """连续 LLM 降级熔断（SPEC §4.9）：比赛标记 failed。"""


@dataclass
class DebateContext:
    config: AppConfig
    topic: Topic
    agents: dict[str, AgentSpec]
    gateway: Gateway
    compressor: Callable[[list[Speech]], dict[str, str]]
    format: DebateFormat
    evidence: dict[Camp, list[EvidenceCard]] = None  # RAG 预检索结果（v1.4；双方各自 top_k）
    retriever: Any | None = None  # v1.8：BM25 检索器（辩手工具 search_evidence 动态检索用）
    web_provider: str | None = None  # v1.8：已解析的联网搜索 provider（none=禁用；工具 search_web 用）
    on_speech: Callable[[Speech], None] | None = None
    on_event: Callable[[dict], None] | None = None
    on_delta: Callable[[int, str, int, str, str], None] | None = None  # (seq, delta, attempt, speaker, camp)；delta="" 表示 attempt 重置信号
    cancel_check: Callable[[], bool] | None = None
    degradation_streak: int = 0
    evidence_usage: dict[str, int] = field(default_factory=dict)  # 书名→全场被引用次数（v1.5 引用纪律）
    speeches: list[Speech] = field(default_factory=list)  # 全场镜像（取消/中断时仍可用）
    graph: "GraphTracker | None" = None  # v2.5 动态图状态机（CLI/测试可不挂）

    def cancelled(self) -> bool:
        return bool(self.cancel_check and self.cancel_check())

    def check_cancel(self) -> None:
        if self.cancelled():
            raise DebateCancelled()

    def emit(self, event: str, **data) -> None:
        """平台事件出口（ARCH §5.3）：phase_start/timer/interrupt/judging/verdict 等。

        v2.5：环节边界与宣判同时是图状态的转折点——先让追踪器吸收这次状态变更并抛出
        graph 事件，再抛原事件，前端因此永远先看到「图变了」再看到「环节变了」。
        """
        if self.graph is not None and self.on_event is not None:
            snap = None
            if event == "phase_start":
                snap = self.graph.feed_phase_start(
                    data.get("phase", 0), data.get("name", ""), data.get("actors"),
                )
            elif event == "phase_end":
                snap = self.graph.feed_phase_end(data.get("phase", 0))
            elif event == "verdict":
                snap = self.graph.feed_verdict(
                    winner=data.get("winner"), rule=data.get("decisive_rule", ""),
                )
            if snap is not None:
                self.on_event({"event": "graph", "data": snap})
        if self.on_event:
            self.on_event({"event": event, "data": data})

    def track_speech(self, speech: Speech) -> None:
        """吸收一条发言到图状态，并推送新的 mermaid 图（每条发言一次，图随辩随新）。"""
        if self.graph is None:
            return
        snap = self.graph.feed_speech(speech)
        if self.on_event:
            self.on_event({"event": "graph", "data": snap})

    def record_degradation(self) -> None:
        self.degradation_streak += 1
        limit = getattr(self.config.model, "degradation_limit", 3)
        if self.degradation_streak >= limit:
            raise DebateAborted(f"连续 {self.degradation_streak} 次 LLM 降级，触发熔断")
