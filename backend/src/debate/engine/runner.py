"""DebateRunner：一场比赛的高层入口。CLI 与（M3 起）Web 服务共用同一引擎路径（CONSTRAINTS B9）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.config import AppConfig
from .agents.loader import AgentSpec, load_agents
from .graph.builder import build_graph
from .graph.state import initial_state
from .llm.gateway import Gateway
from .rag.retriever import EvidenceRetriever
from .rag.websearch import resolve_provider, search_as_cards
from .runtime import DebateAborted, DebateCancelled, DebateContext
from .schema import Camp, DebateFormat, ScoreCard, Speech, Topic, Verdict
from .sim.digest import Digest


@dataclass
class DebateResult:
    status: str  # finished | stopped | failed
    transcript: list[Speech] = field(default_factory=list)
    scorecards: list[ScoreCard] = field(default_factory=list)
    verdict: Verdict | None = None
    error: str | None = None
    machine_marks: list[dict] = field(default_factory=list)  # v1.7 Elenchus 谬误标注
    golden_quotes: list[dict] = field(default_factory=list)  # v2.9 金句摘录
    usage: dict[str, int] = field(default_factory=dict)


def run_debate(
    *,
    topic: Topic,
    fmt: DebateFormat,
    config: AppConfig,
    gateway: Gateway,
    compressor: Callable[[list[Speech]], dict[str, str]],
    agents: dict[str, AgentSpec] | None = None,
    assets_root: Path | None = None,
    on_speech: Callable[[Speech], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
    on_delta: Callable[[int, str, int, str, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> DebateResult:
    if agents is None:
        agents = load_agents(Path(assets_root) if assets_root else Path.cwd())

    # RAG 预检索（v1.4）：双方各自按辩题+己方命题取 top_k 证据卡，全场环节复用
    # v1.4.1：联网搜索结果并入（provider 见 config.web_search；失败静默回落本地）
    # v1.8：retriever / web_provider 一并挂到 ctx——辩手工具循环可动态检索，不再只吃固定预注入
    evidence: dict = {Camp.PRO: [], Camp.CON: []}
    retriever = None
    if config.rag.enabled:
        root = Path(assets_root) if assets_root else Path.cwd()
        retriever = EvidenceRetriever.from_dir(root / config.rag.knowledge_dir)
        evidence = {
            Camp.PRO: retriever.evidence_for(topic, Camp.PRO, top_k=config.rag.top_k),
            Camp.CON: retriever.evidence_for(topic, Camp.CON, top_k=config.rag.top_k),
        }
    web_provider = None
    if config.web_search.enabled:
        provider = config.web_search.provider
        provider = resolve_provider() if provider == "env" else provider
        web_provider = provider
        for camp in (Camp.PRO, Camp.CON):
            web_cards = search_as_cards(
                f"{topic.title} {topic.stance_for(camp)}",
                top_k=config.web_search.top_k, provider=provider,
            )
            evidence[camp] = list(evidence[camp]) + web_cards

    ctx = DebateContext(
        config=config, topic=topic, agents=agents, gateway=gateway,
        compressor=compressor, format=fmt, evidence=evidence, on_speech=on_speech, on_event=on_event, on_delta=on_delta,
        cancel_check=cancel_check, retriever=retriever, web_provider=web_provider,
    )
    # v2.8.1 流式质检：开赛即挂 QC，每条辩手发言落定（track_speech）就分析，
    # 标注实时推 fallacy SSE；评审阶段只收割现成结果。任何失败静默降级。
    qc_stream = None
    elenchus_cfg = getattr(config.tools, "elenchus", None)
    if (elenchus_cfg is not None and bool(getattr(elenchus_cfg, "enabled", False))
            and bool(getattr(elenchus_cfg, "stream", True))
            and getattr(elenchus_cfg, "mode", "inproc") == "inproc"):
        try:
            from .tools.qc_stream import StreamingQC

            qc_stream = StreamingQC(
                check=getattr(elenchus_cfg, "check", "fallacy"),
                sensitivity=getattr(elenchus_cfg, "sensitivity", "strict"),
                timeout=float(getattr(elenchus_cfg, "timeout_seconds", 45.0)),
                parallel=int(getattr(elenchus_cfg, "parallel", 4)),
                on_marks=lambda marks: ctx.emit("fallacy", marks=marks),
            )
            ctx.qc = qc_stream
        except Exception:  # noqa: BLE001 —— 流式质检失败回落评审阶段批量模式
            qc_stream = None
    # v2.9 流式金句摘录：开赛即挂 GoldenQuoteStream，每条辩手发言落定（track_speech）
    # 就异步摘录并实时推 golden_quote SSE；引擎收尾时 harvest 有界收割随结果落库。
    golden_stream = None
    golden_cfg = getattr(config.tools, "golden", None)
    if golden_cfg is not None and bool(getattr(golden_cfg, "enabled", False)):
        try:
            from .tools.golden_stream import GoldenQuoteStream

            golden_stream = GoldenQuoteStream(
                timeout=float(getattr(golden_cfg, "timeout_seconds", 30.0)),
                parallel=int(getattr(golden_cfg, "parallel", 2)),
                max_per_speech=int(getattr(golden_cfg, "max_per_speech", 2)),
                max_total=int(getattr(golden_cfg, "max_total", 16)),
                on_quotes=lambda quotes: ctx.emit("golden_quote", quotes=quotes),
            )
            ctx.golden = golden_stream
        except Exception:  # noqa: BLE001 —— 金句摘录失败不影响辩论本身
            golden_stream = None
    graph = build_graph(ctx, fmt)

    def drain_golden(wait: float) -> list[dict]:
        """终态收割金句：有界等待剩余摘录，任何失败返回已摘到的部分。"""
        if golden_stream is None:
            return []
        try:
            return golden_stream.harvest(wait)
        except Exception:  # noqa: BLE001
            return []

    try:
        final = graph.invoke(initial_state())
        return DebateResult(
            status="finished",
            transcript=final.get("transcript", []),
            scorecards=final.get("scorecards", []),
            verdict=final.get("verdict"),
            machine_marks=final.get("machine_marks", []),
            golden_quotes=drain_golden(8.0),
            usage=dict(gateway.usage),
        )
    except DebateCancelled:
        return DebateResult(
            status="stopped", transcript=list(ctx.speeches),
            golden_quotes=drain_golden(1.0),
            usage=dict(gateway.usage),
        )
    except DebateAborted as exc:
        return DebateResult(
            status="failed", transcript=list(ctx.speeches), error=str(exc),
            golden_quotes=drain_golden(1.0),
            usage=dict(gateway.usage),
        )
    finally:
        if qc_stream is not None:
            qc_stream.close()
        if golden_stream is not None:
            golden_stream.close()
