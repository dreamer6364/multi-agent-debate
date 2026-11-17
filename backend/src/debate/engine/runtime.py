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


class TokenBudgetExceeded(DebateAborted):
    """单赛场 token 预算超限（ISS-014 A-4，SPEC §2.1/§4.9）。

    继承 DebateAborted = 复用既有熔断终态：runner 落 status=failed + error 明细，
    不静默截断、不新增终态。Gateway 在每次调用前检查并抛出（懒加载本模块避免环）。
    """


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
    trace_id: str = ""  # ISS-014 A-5：本场追踪 id（SSE 事件与 LLM 日志关联用）

    def cancelled(self) -> bool:
        return bool(self.cancel_check and self.cancel_check())

    def check_cancel(self) -> None:
        if self.cancelled():
            raise DebateCancelled()

    def emit(self, event: str, **data) -> None:
        """平台事件出口（ARCH §5.3）：phase_start/timer/interrupt/judging/verdict 等。

        ISS-014 A-5：所有事件自动携带本场 trace_id——LLM 调用日志与 SSE 事件可
        按同一 id 关联回放；data 里显式传入的 trace_id 优先（调用方自定义场景）。
        """
        if self.on_event:
            data.setdefault("trace_id", self.trace_id)
            self.on_event({"event": event, "data": data})

    def track_speech(self, speech: Speech) -> None:
        """每条发言落定后：流式质检 + 金句摘录入队（边辩边跑）+ 推送最新一份复盘泳道总结。

        v2.9.4 修复（BE-P1-1）：submit 后立即无等待 poll 一次（harvest(0)）——
        此前 drain/emit 只发生在评审阶段的 harvest 与终态，直播全程的
        fallacy / golden_quote SSE 事件被积压到赛后才一次性补发，实时特性失效。
        harvest(0) 幂等、加锁、不阻塞：完成一条推一条，未完成的留给下次。
        """
        if self.qc is not None:
            try:
                self.qc.submit(speech)
                self.qc.harvest(0)
            except Exception:  # noqa: BLE001 —— 质检任何失败不拖辩论
                pass
        if self.golden is not None:
            try:
                self.golden.submit(speech)
                self.golden.harvest(0)
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
