"""Mermaid 图构建器：**纯函数**，输入 DebateGraphState，输出合法 mermaid 源码。

两张图分别回答两个问题：
  - :func:`build_control_mermaid`  工作流控制图——Agent 被怎么调度（发言 → 裁判判断 → 分支）
  - :func:`build_argument_mermaid` 论证关系图——论点图谱（新主张长出新节点，反驳/质询长出新边）

刻意不把图写在代码里：节点、连线、标签、样式全部遍历 state 现算，
辩论多走一步 state 就多一个节点，前端拿到的永远是"当下这一刻"的图。
"""
from __future__ import annotations

from .debate_state import DebateGraphState, GraphNode

#: 论证图单节点最多展示的论点字数（控制器里已截断，这里按 SVG 可读性再收一次）
_ARG_LABEL_MAX = 20

_CONTROL_STYLE = (
    "  classDef start fill:#eef0fa,stroke:#6d5dfc,stroke-width:2px,color:#333333",
    "  classDef speech fill:#e7ebf2,stroke:#cbd2de,color:#5f697a",
    "  classDef judge fill:#f7f2e3,stroke:#8a6a1c,stroke-width:2px,color:#5a4a12",
    "  classDef branch fill:#eaf3ec,stroke:#3f7d55,color:#2f5c3f",
    "  classDef converge fill:#eef0fa,stroke:#6d5dfc,stroke-width:2px,color:#333333",
    "  classDef term fill:#fdeaea,stroke:#c23a3a,stroke-width:2px,color:#8f2b2b",
    "  classDef active stroke:#6d5dfc,stroke-width:3px,color:#222222",
    "  classDef new stroke:#e0a100,stroke-width:3px",
)

_ARG_STYLE = (
    "  classDef topic fill:#f2f4f8,stroke:#8a93a5,stroke-width:2px,color:#333333",
    "  classDef pro fill:#eef0fa,stroke:#6d5dfc,color:#333333",
    "  classDef con fill:#fdeeee,stroke:#ff6b6b,color:#333333",
    "  classDef neutral fill:#f7f2e3,stroke:#8a6a1c,color:#5a4a12",
    "  classDef active stroke:#6d5dfc,stroke-width:3px,color:#222222",
    "  classDef new stroke:#e0a100,stroke-width:3px",
)

_KIND_CN = {
    "claim": "主张", "rebuttal": "反驳", "question": "质询",
    "support": "补充", "judge": "评审", "verdict": "宣判",
}


def _escape(text: str) -> str:
    """转义成 mermaid 引号标签的安全文本。

    ``#`` 是 mermaid 的实体前缀、``"`` 会提前闭合标签、``|`` 会截断连线文字，
    都可能把整张图变成语法错误，这里逐一替换为等价全角/实体。
    """
    t = str(text or "").replace("\r", " ").replace("\n", " ")
    t = t.replace("#", "＃").replace("|", "｜")
    t = t.replace("<", "‹").replace(">", "›")
    t = t.replace('"', "#quot;")
    return t.strip()


def _node_label(text: str, sub: str = "") -> str:
    """节点标签：主行 + 可选副行（mermaid 用 <br/> 换行）。"""
    main = _escape(text)
    if not sub:
        return main
    return f"{main}<br/>{_escape(sub)}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_control_mermaid(state: DebateGraphState, *, added_flow: list[str] | None = None) -> str:
    """工作流控制图：起始 → 逐条发言 → 裁判判断 → 分支 → … → 收敛 → 终止。"""
    lines = ["flowchart TD", "  %% 工作流控制图 · 由辩论状态实时生成"]
    lines.extend(_CONTROL_STYLE)

    if not state.flow:
        lines.append('  s(["开赛"]):::start')
        return "\n".join(lines)

    active = next((f.id for f in state.flow if f.state == "active"), None)

    for step in state.flow:
        if step.kind == "start":
            lines.append(f'  {step.id}(["{_escape(step.label)}"]):::start')
            continue
        cls = {
            "speech": "speech", "judge": "judge", "branch": "branch",
            "converge": "converge", "terminate": "term",
        }.get(step.kind, "speech")
        label = _node_label(step.label, step.detail)
        if step.kind in ("judge", "branch", "converge"):
            lines.append(f'  {step.id}{{"{label}"}}:::{cls}')
        elif step.kind == "terminate":
            lines.append(f'  {step.id}(["{label}"]):::{cls}')
        else:
            lines.append(f'  {step.id}["{label}"]:::{cls}')

    # 连线：按 flow 顺序推进；分支节点挂语义标签，收敛用粗箭头
    for prev, cur in zip(state.flow, state.flow[1:]):
        if cur.kind == "branch":
            # 分支语义已经写在分支节点自己身上（继续下一轮/追加质询/提前结束），
            # 边上不再重复一遍同样的字，只标「判定」表明是裁判做出的选择
            lines.append(f"  {prev.id} -->|{_escape('判定')}| {cur.id}")
        elif cur.kind == "converge":
            lines.append(f"  {prev.id} ==>|{_escape('收束')}| {cur.id}")
        elif cur.kind == "terminate" and prev.kind == "converge":
            lines.append(f"  {prev.id} -->|{_escape('判定')}| {cur.id}")
        else:
            lines.append(f"  {prev.id} --> {cur.id}")

    fresh = [f.id for f in state.flow if f.id in set(added_flow or [])]
    if active:
        lines.append(f"  class {active} active")
    if fresh:
        lines.append(f"  class {','.join(fresh)} new")
    return "\n".join(lines)


def _node_class(node: GraphNode) -> str:
    if node.camp == "pro":
        return "pro"
    if node.camp == "con":
        return "con"
    return "neutral"


def build_argument_mermaid(state: DebateGraphState, *, added_nodes: list[str] | None = None) -> str:
    """论证关系图：辩题 ↔ 论点节点；支持实线、反驳/质询虚线，当前论点高亮。"""
    added = set(added_nodes or [])
    lines = ["flowchart LR", "  %% 论证关系图 · 随双方发言持续生长"]
    lines.extend(_ARG_STYLE)

    title = _escape(_clip(state.topic or "辩题", 26))
    lines.append(f'  T(["{title}"]):::topic')

    if not state.nodes:
        return "\n".join(lines)

    by_id = {n.id: n for n in state.nodes}
    for node in state.nodes:
        kind_cn = _KIND_CN.get(node.kind, "发言")
        head = f"{node.label}"
        sub = f"{kind_cn} · {node.round} 轮"
        cls = _node_class(node)
        lines.append(f'  {node.id}["{_node_label(head, sub)}"]:::{cls}')

    # 第一条发言与辩题建立引用；其余全靠论点之间的关系边
    first = state.nodes[0].id
    lines.append(f'  T -->|{_escape("切入")}| {first}')

    for edge in state.edges:
        if edge.src not in by_id or edge.dst not in by_id:
            continue
        if edge.kind == "flow":
            # 主席/评委的时序连线：不标关系词，避免与"支持/反驳"的语义线混淆
            lines.append(f"  {edge.src} --> {edge.dst}")
            continue
        label = _escape(edge.label)
        if edge.kind == "support":
            lines.append(f"  {edge.src} -->|{label}| {edge.dst}")
        elif edge.kind == "query":
            lines.append(f"  {edge.src} -.->|{label}| {edge.dst}")
        else:
            lines.append(f"  {edge.src} -.->|{label}| {edge.dst}")

    if state.nodes:
        lines.append(f"  class {state.nodes[-1].id} active")
    fresh = [n.id for n in state.nodes if n.id in added]
    if fresh:
        lines.append(f"  class {','.join(fresh)} new")
    return "\n".join(lines)


def build_views(state: DebateGraphState, *, added_nodes: list[str] | None = None,
                added_flow: list[str] | None = None) -> dict[str, str]:
    """一次产出两种视图（供 SSE graph 事件直接使用）。"""
    return {
        "control": build_control_mermaid(state, added_flow=added_flow),
        "argument": build_argument_mermaid(state, added_nodes=added_nodes),
    }
