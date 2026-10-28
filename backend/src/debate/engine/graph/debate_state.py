"""辩论图状态（DebateGraphState）与演进追踪器。

设计约束（核心需求）：**不写死任何一张固定流程图**。
辩论每推进一步（一条 Agent 发言 / 一次裁判判断），本模块把这一步"吸收"进状态：
新增论点节点、判定它与既有论点的支持/攻击关系、推进回合号、记录分支决策；
随后由 ``mermaid.py`` 依据状态现场生成图源码，前端替换渲染。

因此本文件只关心"状态怎么变"，不关心 mermaid 语法；语义分层：
    DebateGraphState —— 可序列化的辩论状态快照（回合/双方 Agent/裁判/当前论点/关系/收敛/终止/分支）
    GraphTracker     —— 把 Speech / 环节事件流折进状态的增量机
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..agents.loader import ROLE_DEFS
from ..schema import Camp, Speech
from ..sim.conduct import strip_markdown_noise

# ---- 关系判定词表（启发式，宁可粗不可缺：图要"长得出边"，不追求语义完美） ----

ATTACK_HINTS = (
    "不成立", "站不住", "然而", "但是", "恰恰相反", "相反", "忽略了", "忽视",
    "误判", "错误", "漏洞", "反驳", "并不", "难以", "无法", "以偏概全", "偷换",
    "滑坡", "倒果为因", "循环论证", "诉诸", "混淆", "质疑", "反而",
)
SUPPORT_HINTS = (
    "支持", "印证", "正如", "同意", "补充", "进一步", "同样", "一致",
    "佐证", "呼应", "延伸", "深化",
)
QUESTION_HINTS = ("？", "?", "请问", "是否", "为什么", "如何", "难道")

#: 质询 / 攻辩环节：发言天然是"问"，关系边按 query 处理
QUESTION_PHASES = (3, 5)

_LABEL_MAX = 16
_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def speaker_cn(role_id: str) -> str:
    """role_id → 中文位次（pro-1 → 正方一辩）；未知角色原样返回。"""
    spec = ROLE_DEFS.get(role_id)
    if spec is None:
        return role_id
    return spec.position_cn.split(" · ")[0]


def _clip(text: str, limit: int = _LABEL_MAX) -> str:
    """压缩成图里能读的一行：去 markdown 噪声、取首句、超长截断加省略号。"""
    clean = strip_markdown_noise(text).strip()
    clean = re.sub(r"\s+", " ", clean)
    first = re.split(r"[。！？!?；;]", clean, maxsplit=1)[0].strip() or clean
    if len(first) <= limit:
        return first
    return first[: limit - 1] + "…"


def _hits(text: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if w in text:
            return w
    return None


@dataclass
class GraphNode:
    """论证关系图里的一个论点节点。"""

    id: str
    kind: str  # claim | rebuttal | question | support | judge | verdict
    camp: str  # pro | con | neutral
    speaker: str
    label: str
    detail: str
    seq: int
    phase: int
    round: int

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "camp": self.camp,
            "speaker": self.speaker, "label": self.label, "detail": self.detail,
            "seq": self.seq, "phase": self.phase, "round": self.round,
        }


@dataclass
class GraphEdge:
    """论点之间的关系：support 实线 / attack 虚线 / query 虚线带问号。

    label 自带来源发言人（如「反方二辩 反驳」），图上明确"谁指向谁"。
    """

    id: str
    src: str
    dst: str
    kind: str  # support | attack | query | flow
    label: str

    def to_dict(self) -> dict:
        return {"id": self.id, "src": self.src, "dst": self.dst,
                "kind": self.kind, "label": self.label}


@dataclass
class DebateGraphState:
    """一次辩论的图状态（可直接 JSON 序列化给前端）。"""

    topic: str = ""
    round: int = 0
    pro_agent: str = ""
    con_agent: str = ""
    moderator: str = "chair"
    current_claim: str = ""
    phase: int = 0
    phase_name: str = ""
    converged: bool = False
    terminated: bool = False
    branch: str = "next_round"  # next_round | extra_query | early_finish | terminate
    winner: str | None = None
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic, "round": self.round,
            "pro_agent": self.pro_agent, "con_agent": self.con_agent,
            "moderator": self.moderator, "current_claim": self.current_claim,
            "phase": self.phase, "phase_name": self.phase_name,
            "converged": self.converged, "terminated": self.terminated,
            "branch": self.branch, "winner": self.winner,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    def summary(self) -> dict:
        """下行到前端的精简状态（每轮都推一次，不带节点明细——图源码里已经有全量信息）。"""
        return {
            "topic": self.topic, "round": self.round,
            "pro_agent": self.pro_agent, "con_agent": self.con_agent,
            "moderator": self.moderator, "current_claim": self.current_claim,
            "phase": self.phase, "phase_name": self.phase_name,
            "converged": self.converged, "terminated": self.terminated,
            "branch": self.branch, "winner": self.winner,
            "node_count": len(self.nodes), "edge_count": len(self.edges),
        }


class GraphTracker:
    """把辩论过程折进 DebateGraphState，并在每次变更后产出一个快照事件。

    调用点（见 ``runtime.DebateContext``）：
      - 每条发言落定 → :meth:`feed_speech`
      - 环节开始/结束 → :meth:`feed_phase_start` / :meth:`feed_phase_end`
      - 宣判 → :meth:`feed_verdict`
    所有方法返回"增量描述"（新增节点/边 id、当前活跃节点），供前端做变更标注。
    """

    def __init__(self, *, topic: str = "", votes_style: str = "") -> None:
        self.state = DebateGraphState(topic=topic)
        self.version = 0
        self.history: list[dict] = []
        self._seq = 0
        self._last_camp: str | None = None
        self._round_camps: set[str] = set()
        self._last_node_by_camp: dict[str, str] = {}

    # ---------- 内部：id 分配与连线 ----------

    def _nid(self) -> str:
        self._seq += 1
        return f"n{self._seq}"

    def _last_node(self, camp: str) -> str | None:
        return self._last_node_by_camp.get(camp)

    # ---------- 内部：关系判定 ----------

    def _classify(self, speech: Speech, prev_own: str | None, prev_opp: str | None) -> tuple[str, str]:
        """返回 (节点类型, 附加说明)。质询环节的发言一律算 question。"""
        text = speech.text
        # 主席/评委是中立角色：他们没有"支持/反驳"的对象，别把致辞标成"补充"
        if speech.camp is Camp.NEUTRAL:
            return "neutral", ("评审" if speech.phase in (9, 10) else "主持")
        if speech.phase in QUESTION_PHASES or (
            speech.camp is not Camp.NEUTRAL and _hits(text, QUESTION_HINTS) and len(text) < 120
        ):
            return "question", "提问"
        attack = _hits(text, ATTACK_HINTS)
        support = _hits(text, SUPPORT_HINTS)
        # 立论环节的第一条发言是"主张"而非"补充"——先有论点，才谈得上支持谁
        if speech.phase == 2 and attack is None:
            return "claim", "立论主张"
        if support and attack is None and prev_own:
            return "support", f"补充（{support}）"
        if attack:
            return "rebuttal", f"攻击（{attack}）"
        if speech.phase in (2, 8) or prev_opp is None:
            return "claim", "主张"
        return "rebuttal", "回应"

    def _connect(self, node: GraphNode, speech: Speech,
                 prev_own: str | None, prev_opp: str | None,
                 prev_any: str | None = None) -> list[GraphEdge]:
        """按发言对象挑目标节点：优先对方上一条论点，其次本方上一条论点。

        目标必须取自"本条之前"的节点——先登记本条再连线会让首条发言自己指向自己。
        主席/评委这类中立发言没有攻防对象，改按时间顺序接到上一条（flow 边），
        否则它们会变成图里漂浮的孤立节点。
        边标签统一带来源发言人（如「反方二辩 反驳」），自由辩论交锋再密集也能看清谁指向谁。
        """
        edges: list[GraphEdge] = []
        speaker = speaker_cn(speech.speaker)
        if speech.camp is Camp.NEUTRAL:
            if prev_any:
                edges.append(GraphEdge(f"e{len(self.state.edges) + 1}", node.id, prev_any, "flow", speaker))
            return edges
        if node.kind == "question":
            target = prev_opp or prev_own
        elif node.kind == "support":
            target = prev_own or prev_opp
        else:
            target = prev_opp or prev_own
        if not target:
            return edges
        kind = {"question": "query", "support": "support"}.get(node.kind, "attack")
        base = {"query": "质询", "support": "支持"}.get(kind, "反驳")
        edges.append(GraphEdge(f"e{len(self.state.edges) + 1}", node.id, target, kind, f"{speaker} {base}"))
        return edges

    # ---------- 对外：状态推进 ----------

    def feed_speech(self, speech: Speech) -> dict:
        """吸收一条发言：新论点节点 + 攻防连线 + 回合推进。"""
        camp = speech.camp.value
        added_nodes: list[str] = []
        added_edges: list[str] = []

        # 回合推进：正反交替即进入下一轮（同方连续发言仍算同一轮，如质询多问）
        if self._last_camp is not None and self._last_camp != camp:
            if {Camp.PRO.value, Camp.CON.value} <= self._round_camps:
                self._close_round()
        if self.state.round == 0:
            self.state.round = 1
        self._round_camps.add(camp)
        self._last_camp = camp

        prev_own = self._last_node(camp)
        prev_opp = self._last_node((Camp.CON if speech.camp is Camp.PRO else Camp.PRO).value)
        prev_any = self.state.nodes[-1].id if self.state.nodes else None
        kind, note = self._classify(speech, prev_own, prev_opp)
        label = _clip(speech.text)
        detail = f"{speech.phase_name} · {speaker_cn(speech.speaker)} · 第 {speech.seq} 条"
        node = GraphNode(
            id=self._nid(), kind=kind, camp=camp, speaker=speech.speaker,
            label=label, detail=detail, seq=speech.seq, phase=speech.phase, round=self.state.round,
        )
        self.state.nodes.append(node)
        added_nodes.append(node.id)
        self._last_node_by_camp[camp] = node.id

        for edge in self._connect(node, speech, prev_own, prev_opp, prev_any):
            self.state.edges.append(edge)
            added_edges.append(edge.id)

        self.state.current_claim = label
        self.state.phase = speech.phase
        self.state.phase_name = speech.phase_name
        if camp == Camp.PRO.value:
            self.state.pro_agent = speech.speaker
        elif camp == Camp.CON.value:
            self.state.con_agent = speech.speaker

        return self._snapshot(added_nodes, added_edges)

    def _close_round(self) -> None:
        """一轮结束：只推进回合号与分支语义。

        控制图里的「裁判判断」节点统一由环节边界（:meth:`feed_phase_end`）产生——
        若这里也插一个，每个环节会连续出现两枚裁判节点，读图的人分不清哪枚在调度。
        """
        self.state.round += 1
        self.state.branch = "next_round"
        self._round_camps = set()

    def feed_phase_start(self, phase: int, name: str, actors: list[str] | None = None) -> dict:
        """环节开始＝状态推进到新环节（actor 信息保留在 state 里供前端展示）。"""
        self.state.phase = phase
        self.state.phase_name = name
        return self._snapshot([], [])

    def feed_phase_end(self, phase: int) -> dict:
        """环节结束＝一次裁判调度判断（继续下一环节 / 追加质询 / 进入宣判）。"""
        if phase in QUESTION_PHASES:
            branch, label = "extra_query", "追加质询"
        elif phase == 10:
            branch, label = "next_round", "进入致辞"
        elif phase == 11:
            branch, label = "terminate", "结束"
        else:
            branch, label = "next_round", "继续下一轮"
        self.state.branch = branch
        return self._snapshot([], [])

    def feed_verdict(self, *, winner: str | None = None, rule: str = "") -> dict:
        """宣判：收敛 + 终止。"""
        self.state.converged = True
        self.state.terminated = True
        self.state.winner = winner
        self.state.branch = "terminate"
        return self._snapshot([], [])

    def feed_status(self, status: str, error: str | None = None) -> dict | None:
        """异常/中止收尾：标记终止，保证状态有明确出口。"""
        if status not in ("failed", "stopped"):
            return None
        if self.state.terminated:
            return None
        self.state.terminated = True
        self.state.branch = "early_finish"
        return self._snapshot([], [])

    # ---------- 快照 ----------

    def _snapshot(self, added_nodes: list[str], added_edges: list[str]) -> dict:
        from .mermaid import build_views

        self.version += 1
        payload = {
            "version": self.version,
            "round": self.state.round,
            "state": self.state.summary(),
            "views": build_views(self.state, added_nodes=added_nodes),
            "active_node": self.state.nodes[-1].id if self.state.nodes else None,
            "added_nodes": list(added_nodes),
            "added_edges": list(added_edges),
            # 前端只在画布上标注"新增攻击边/支持边"，需要边类型而不只是 id
            "added_edge_kinds": [e.kind for e in self.state.edges if e.id in set(added_edges)],
            "converged": self.state.converged,
            "terminated": self.state.terminated,
            "branch": self.state.branch,
        }
        self.history.append(payload)
        return payload
