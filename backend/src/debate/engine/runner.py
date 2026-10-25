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
    evidence: dict = {Camp.PRO: [], Camp.CON: []}
    if config.rag.enabled:
        root = Path(assets_root) if assets_root else Path.cwd()
        retriever = EvidenceRetriever.from_dir(root / config.rag.knowledge_dir)
        evidence = {
            Camp.PRO: retriever.evidence_for(topic, Camp.PRO, top_k=config.rag.top_k),
            Camp.CON: retriever.evidence_for(topic, Camp.CON, top_k=config.rag.top_k),
        }

    ctx = DebateContext(
        config=config, topic=topic, agents=agents, gateway=gateway,
        compressor=compressor, format=fmt, evidence=evidence, on_speech=on_speech, on_event=on_event, on_delta=on_delta,
        cancel_check=cancel_check,
    )
    graph = build_graph(ctx, fmt)
    try:
        final = graph.invoke(initial_state())
        return DebateResult(
            status="finished",
            transcript=final.get("transcript", []),
            scorecards=final.get("scorecards", []),
            verdict=final.get("verdict"),
            usage=dict(gateway.usage),
        )
    except DebateCancelled:
        return DebateResult(
            status="stopped", transcript=list(ctx.speeches), usage=dict(gateway.usage),
        )
    except DebateAborted as exc:
        return DebateResult(
            status="failed", transcript=list(ctx.speeches), error=str(exc), usage=dict(gateway.usage),
        )
