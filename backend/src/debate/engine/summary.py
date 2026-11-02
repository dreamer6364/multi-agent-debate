"""复盘泳道总结（v2.7）：按「阶段 × 泳道（正方 / 反方 / 公共场域）」汇总一场辩论。

数据全部来自实录推导，不引入额外 LLM 调用：
  - 论点分类与攻防连线由内置 :class:`PointingIndex` 完成（启发式，与全场一致）；
  - 公共场域：接收方/计时提示按环节语义生成，违规打断、质检谬误、发言降级、主持/评审进特殊标注。

两条输出通道共用本模块的纯函数：
  - 直播：``runtime.track_speech`` 每条发言后推 `swimlane` SSE 事件（断线由 event_bus 补发最新一份）；
  - 归档：``api.debates`` 详情接口按落库实录现算，旧场次无需迁移即可获得。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace

from .agents.loader import ROLE_DEFS
from .schema import Camp, Speech

#: 内容摘录长度（首句优先，超长截断）
_CONTENT_MAX = 48
_NOTE_MAX = 30

_REL_CN = {"attack": "反驳", "query": "质询", "support": "支持", "flow": "承接"}

#: 各环节「动作」措辞（按论点类型微调）
_ACTION_BY_KIND = {
    "claim": "立论主张", "rebuttal": "反驳拆解", "question": "质询提问",
    "support": "补充论证", "neutral": "主持推进",
}

#: 公共场域：接收方与计时提示（按环节语义，静态规则）
_RECEIVERS = {
    1: "全场", 2: "评委＋对方全员", 3: "对方辩手", 4: "评委＋对方全员",
    5: "对方辩手", 6: "评委＋双方全员", 7: "全场", 8: "评委＋对方全员",
    9: "全场", 10: "全场", 11: "全场",
}
_TIMER = {7: "逐轮独立计时"}

# ---- 关系判定词表（启发式，宁可粗不可缺：指向关系要"连得出"，不追求语义完美） ----

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


def speaker_cn(role_id: str) -> str:
    """role_id → 中文位次（pro-1 → 正方一辩）；未知角色原样返回。"""
    spec = ROLE_DEFS.get(role_id)
    if spec is None:
        return role_id
    return spec.position_cn.split(" · ")[0]


def _hits(text: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if w in text:
            return w
    return None


@dataclass
class _Node:
    """指向索引里的一个论点节点。"""

    id: str
    kind: str  # claim | rebuttal | question | support | neutral
    camp: str  # pro | con | neutral
    speaker: str
    seq: int
    phase: int


@dataclass
class _Edge:
    """论点之间的关系：attack 反驳 / query 质询 / support 支持 / flow 时序。"""

    id: str
    src: str
    dst: str
    kind: str
    label: str


class PointingIndex:
    """把全场实录折算成「论点节点 + 攻防指向边」的轻量索引。

    只服务复盘总结：不需要快照/版本/mermaid，feed 一遍之后查 nodes/edges 即可。
    """

    def __init__(self) -> None:
        self.nodes: list[_Node] = []
        self.edges: list[_Edge] = []
        self._seq = 0
        self._last_node_by_camp: dict[str, str] = {}

    def _nid(self) -> str:
        self._seq += 1
        return f"n{self._seq}"

    # ---------- 内部：关系判定 ----------

    def _classify(self, speech: Speech, prev_own: str | None) -> str:
        """返回节点类型。主席/评委是中立角色；质询环节的发言一律算 question。"""
        text = speech.text
        if speech.camp is Camp.NEUTRAL:
            return "neutral"
        if speech.phase in QUESTION_PHASES or (
            _hits(text, QUESTION_HINTS) and len(text) < 120
        ):
            return "question"
        attack = _hits(text, ATTACK_HINTS)
        support = _hits(text, SUPPORT_HINTS)
        # 立论环节的第一条发言是"主张"而非"补充"——先有论点，才谈得上支持谁
        if speech.phase == 2 and attack is None:
            return "claim"
        if support and attack is None and prev_own:
            return "support"
        if attack:
            return "rebuttal"
        if speech.phase in (2, 8) or prev_own is None:
            return "claim"
        return "rebuttal"

    def _connect(self, node: _Node, speech: Speech,
                 prev_own: str | None, prev_opp: str | None,
                 prev_any: str | None) -> list[_Edge]:
        """按发言对象挑目标节点：优先对方上一条论点，其次本方上一条论点。

        目标必须取自"本条之前"的节点——先登记本条再连线会让首条发言自己指向自己。
        中立发言没有攻防对象，按时间顺序接到上一条（flow 边）。
        边标签统一带来源发言人（如「反方二辩 反驳」），明确"谁指向谁"。
        """
        speaker = speaker_cn(speech.speaker)
        if speech.camp is Camp.NEUTRAL:
            if prev_any:
                return [_Edge(f"e{len(self.edges) + 1}", node.id, prev_any, "flow", speaker)]
            return []
        if node.kind == "question":
            target = prev_opp or prev_own
        elif node.kind == "support":
            target = prev_own or prev_opp
        else:
            target = prev_opp or prev_own
        if not target:
            return []
        kind = {"question": "query", "support": "support"}.get(node.kind, "attack")
        base = {"query": "质询", "support": "支持"}.get(kind, "反驳")
        return [_Edge(f"e{len(self.edges) + 1}", node.id, target, kind, f"{speaker} {base}")]

    # ---------- 对外：吸收一条发言 ----------

    def feed_speech(self, speech: Speech) -> None:
        camp = speech.camp.value
        prev_own = self._last_node_by_camp.get(camp)
        opp_camp = (Camp.CON if speech.camp is Camp.PRO else Camp.PRO).value
        prev_opp = self._last_node_by_camp.get(opp_camp)
        prev_any = self.nodes[-1].id if self.nodes else None
        kind = self._classify(speech, prev_own)
        node = _Node(
            id=self._nid(), kind=kind, camp=camp, speaker=speech.speaker,
            seq=speech.seq, phase=speech.phase,
        )
        self.nodes.append(node)
        self._last_node_by_camp[camp] = node.id
        self.edges.extend(self._connect(node, speech, prev_own, prev_opp, prev_any))


def _clip(text: str, limit: int = _CONTENT_MAX) -> str:
    """取首句可读摘录：去 markdown 噪声、压空白、超长截断。"""
    from .sim.conduct import strip_markdown_noise

    clean = re.sub(r"\s+", " ", strip_markdown_noise(text).strip())
    first = re.split(r"[。！？!?；;]", clean, maxsplit=1)[0].strip() or clean
    return first if len(first) <= limit else first[: limit - 1] + "…"


def _entry(speaker: str, action: str, content: str,
           points_to: list[str] | None = None,
           pointed_by: list[str] | None = None,
           seq: int | None = None) -> dict:
    return {
        "speaker": speaker, "action": action, "content": content,
        "points_to": points_to or [], "pointed_by": pointed_by or [],
        "seq": seq,
    }


def _mark_name(mark: object) -> str:
    """谬误标注兼容对象/字典两种形态（引擎内是 pydantic，落库后是 dict）。"""
    if isinstance(mark, dict):
        return str(mark.get("name", ""))
    return str(getattr(mark, "name", ""))


def _normalize(speeches: list) -> list:
    """鸭子类型归一化：camp 字符串转 Camp 枚举，其余原样（REST 落库行是 SimpleNamespace）。"""
    fixed: list = []
    for s in speeches:
        camp = s.camp
        if isinstance(camp, str):
            camp = Camp(camp)
            s = SimpleNamespace(**{**getattr(s, "__dict__", {}), **{
                "seq": s.seq, "phase": s.phase, "phase_name": s.phase_name,
                "speaker": s.speaker, "camp": camp, "text": s.text,
                "side_notes": getattr(s, "side_notes", []) or [],
                "interrupted": getattr(s, "interrupted", False),
                "fallacies": getattr(s, "fallacies", []) or [],
            }})
        fixed.append(s)
    return fixed


def build_swimlane(speeches: list[Speech], *, topic: str = "") -> dict:
    """把全场实录折算成泳道总结：rows[phase] = {pro, con, public}。

    攻防指向来自 :class:`PointingIndex` 的连线，因此「谁指向谁」全场口径一致。
    """
    speech_list = _normalize(speeches)
    index = PointingIndex()
    for speech in speech_list:
        index.feed_speech(speech)
    nodes = index.nodes
    node_by_id = {n.id: n for n in nodes}
    node_by_seq = {n.seq: n for n in nodes}
    phase_names = {s.phase: s.phase_name for s in speech_list}

    out_edges: dict[str, list] = {}
    in_edges: dict[str, list] = {}
    for edge in index.edges:
        out_edges.setdefault(edge.src, []).append(edge)
        in_edges.setdefault(edge.dst, []).append(edge)

    def rel(edge_kind: str, who: str) -> str:
        return f"{who}（{_REL_CN.get(edge_kind, edge_kind)}）"

    # 按环节聚合（保持出场顺序）
    phase_order: list[int] = []
    by_phase: dict[int, list[Speech]] = {}
    for speech in speech_list:
        if speech.phase not in by_phase:
            by_phase[speech.phase] = []
            phase_order.append(speech.phase)
        by_phase[speech.phase].append(speech)

    rows: list[dict] = []
    for phase in phase_order:
        phase_speeches = by_phase[phase]
        name = phase_speeches[0].phase_name or f"环节 {phase}"
        pro: list[dict] = []
        con: list[dict] = []
        notes: list[str] = []
        # 早前环节的立论在本环节被攻击 → 在受害者的阵营一格补「被指向」条目
        victims: list[tuple[str, dict]] = []
        # 质询/攻辩/自由辩论：按阵营合并成「轮换」条目，突出交锋节奏
        grouped_phase = phase in QUESTION_PHASES or phase == 7

        if grouped_phase:
            for camp, bucket in ((Camp.PRO, pro), (Camp.CON, con)):
                camp_speeches = [s for s in phase_speeches if s.camp is camp]
                if not camp_speeches:
                    continue
                chain = " → ".join(speaker_cn(s.speaker) for s in camp_speeches)
                if phase == 7:
                    action = "反驳对方上一轮观点＋补证己方逻辑"
                    pointing = ["点对点针对对方上一位发言辩手"]
                else:
                    action = "质询提问＋限时作答"
                    pointing = ["集中质询对方立论"]
                bucket.append(_entry(
                    speaker=f"{'正方' if camp is Camp.PRO else '反方'}轮换",
                    action=action,
                    content=f"共 {len(camp_speeches)} 条交锋：{chain}",
                    points_to=pointing,
                    pointed_by=pointing,
                    seq=camp_speeches[0].seq,
                ))

        for speech in phase_speeches:
            node = node_by_seq.get(speech.seq)
            speaker = speaker_cn(speech.speaker)
            if speech.camp is Camp.NEUTRAL:
                notes.append(f"{speaker}：{_clip(speech.text, _NOTE_MAX)}")
            elif not grouped_phase:
                action = _ACTION_BY_KIND.get(node.kind if node else "", "发言")
                if phase == 2 and node is not None and node.kind == "claim":
                    action = "开篇立论"
                elif phase == 8:
                    action = "总结陈词"
                elif phase == 6:
                    action = "阶段小结"
                points_to = [rel(e.kind, speaker_cn(node_by_id[e.dst].speaker))
                             for e in out_edges.get(node.id, [])] if node else []
                pointed_by = [rel(e.kind, speaker_cn(node_by_id[e.src].speaker))
                              for e in in_edges.get(node.id, [])] if node else []
                entry = _entry(speaker, action, _clip(speech.text),
                               points_to, pointed_by, speech.seq)
                (pro if speech.camp is Camp.PRO else con).append(entry)

            # 本条发言攻击了早前环节的论点 → 给受害者的阵营记一条「被指向」
            if node is not None and speech.camp is not Camp.NEUTRAL:
                for edge in out_edges.get(node.id, []):
                    if edge.kind not in ("attack", "query"):
                        continue
                    target = node_by_id.get(edge.dst)
                    if target is None or target.phase >= phase:
                        continue
                    if target.camp in (Camp.NEUTRAL.value, speech.camp.value):
                        continue
                    victims.append((target.camp, _entry(
                        speaker=speaker_cn(target.speaker),
                        action="被指向",
                        content=f"{phase_names.get(target.phase, '此前')}的论点被 {speaker} 攻击",
                        seq=speech.seq,
                    )))

            if speech.interrupted:
                notes.append(f"{speaker} 违规打断")
            for mark in speech.fallacies or []:
                notes.append(f"{speaker} 被质检标注「{_mark_name(mark)}」")
            if any("降级" in note for note in (speech.side_notes or [])):
                notes.append(f"{speaker} 发言降级（占位）")

        for camp, entry in victims:
            # victims 的 camp 是节点里的字符串值（pro/con/neutral）
            (pro if camp == Camp.PRO.value else con).append(entry)

        rows.append({
            "phase": phase,
            "name": name,
            "pro": pro,
            "con": con,
            "public": {
                "receivers": _RECEIVERS.get(phase, "全场"),
                "timer": _TIMER.get(phase, "剩余 30 秒提示"),
                "notes": notes,
            },
        })

    return {"topic": topic, "rows": rows}
