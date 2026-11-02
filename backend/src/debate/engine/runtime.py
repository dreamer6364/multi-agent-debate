"""引擎运行时上下文与流程级异常（ARCH §4.2 DebateState 之外的"闭包依赖"）。

依赖（config/topic/agents/gateway/回调）走闭包注入而非图状态，
保证图状态可序列化、节点纯净（CONSTRAINTS B2：引擎不感知 Web）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .llm.gateway import Gateway
from .rag.corpus import EvidenceCard
from .schema import Camp, DebateFormat, Speech, Topic
from .agents.loader import AgentSpec
from ..core.config import AppConfig


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
    qc: Any | None = None  # v2.8.1 流式质检（StreamingQC）：每条发言落定即分析；CLI/测试可不挂
    golden: Any | None = None  # v2.9 流式金句摘录（GoldenQuoteStream）：每条发言落定即摘录；CLI/测试可不挂

    def cancelled(self) -> bool:
        return bool(self.cancel_check and self.cancel_check())

    def check_cancel(self) -> None:
        if self.cancelled():
            raise DebateCancelled()

    def emit(self, event: str, **data) -> None:
        """平台事件出口（ARCH §5.3）：phase_start/timer/interrupt/judging/verdict 等。"""
        if self.on_event:
            self.on_event({"event": event, "data": data})

    def track_speech(self, speech: Speech) -> None:
        """每条发言落定后：流式质检 + 金句摘录入队（边辩边跑）+ 推送最新一份复盘泳道总结。"""
        if self.qc is not None:
            try:
                self.qc.submit(speech)
            except Exception:  # noqa: BLE001 —— 质检任何失败不拖辩论
                pass
        if self.golden is not None:
            try:
                self.golden.submit(speech)
            except Exception:  # noqa: BLE001 —— 金句摘录任何失败不拖辩论
                pass
        if self.on_event:
            from .summary import build_swimlane

            self.on_event({"event": "swimlane", "data": build_swimlane(self.speeches, topic=self.topic.title)})

    def record_degradation(self) -> None:
        self.degradation_streak += 1
        limit = getattr(self.config.model, "degradation_limit", 3)
        if self.degradation_streak >= limit:
            raise DebateAborted(f"连续 {self.degradation_streak} 次 LLM 降级，触发熔断")
