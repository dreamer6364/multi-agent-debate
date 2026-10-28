"""DebateRunner：一场比赛的高层入口。CLI 与（M3 起）Web 服务共用同一引擎路径（CONSTRAINTS B9）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.config import AppConfig
from .agents.loader import AgentSpec, load_agents
from .graph.builder import build_graph
from .graph.debate_state import GraphTracker
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
    usage: dict[str, int] = field(default_factory=dict)
    graph_snapshots: list[dict] = field(default_factory=list)  # v2.5 逐轮图快照（归档回看用）


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
        # v2.5：图状态机与辩论同生共死——发言/环节/宣判都会喂给它，它负责现场生成 mermaid
        graph=GraphTracker(topic=topic.title),
    )
    graph = build_graph(ctx, fmt)
    try:
        final = graph.invoke(initial_state())
        return DebateResult(
            status="finished",
            transcript=final.get("transcript", []),
            scorecards=final.get("scorecards", []),
            verdict=final.get("verdict"),
            machine_marks=final.get("machine_marks", []),
            usage=dict(gateway.usage),
            graph_snapshots=list(ctx.graph.history) if ctx.graph else [],
        )
    except DebateCancelled:
        return DebateResult(
            status="stopped", transcript=list(ctx.speeches), usage=dict(gateway.usage),
            graph_snapshots=list(ctx.graph.history) if ctx.graph else [],
        )
    except DebateAborted as exc:
        return DebateResult(
            status="failed", transcript=list(ctx.speeches), error=str(exc), usage=dict(gateway.usage),
            graph_snapshots=list(ctx.graph.history) if ctx.graph else [],
        )
