"""复盘泳道总结（v2.7）：按「阶段 × 泳道（正方 / 反方 / 公共场域）」汇总一场辩论。

数据全部来自实录与图状态推导，不引入额外 LLM 调用：
  - 复用 :class:`GraphTracker` 的论点分类与攻防连线——「指向 / 被指向」与论证关系图完全一致；
  - 公共场域：接收方/计时提示按环节语义生成，违规打断、质检谬误、发言降级、主持/评审进特殊标注。

两条输出通道共用本模块的纯函数：
  - 直播：``runtime.track_speech`` 每条发言后推 `swimlane` SSE 事件（断线由 event_bus 补发最新一份）；
  - 归档：``api.debates`` 详情接口按落库实录现算，旧场次无需迁移即可获得。
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from .schema import Camp, Speech
from .graph.debate_state import QUESTION_PHASES, GraphTracker, speaker_cn

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

    攻防指向来自 GraphTracker 的连线（与论证关系图同源），
    因此「谁指向谁」在总结表和动态图里永远一致。
    """
    tracker = GraphTracker(topic=topic)
    for speech in _normalize(speeches):
        tracker.feed_speech(speech)
    speech_list = _normalize(speeches)
    nodes = tracker.state.nodes
    node_by_id = {n.id: n for n in nodes}
    node_by_seq = {n.seq: n for n in nodes}
    phase_names = {s.phase: s.phase_name for s in speech_list}

    out_edges: dict[str, list] = {}
    in_edges: dict[str, list] = {}
    for edge in tracker.state.edges:
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
        victims: list[tuple[Camp, dict]] = []
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
            # （GraphNode.camp 存的是 camp 字符串值，比较用 .value）
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
