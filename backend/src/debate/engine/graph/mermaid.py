"""Mermaid 图构建器：**纯函数**，输入 DebateGraphState，输出合法 mermaid 源码。

v2.6.2 起只保留一张论证关系图（工作流控制图已按需求删除）：
    :func:`build_argument_mermaid` 论点图谱——新主张长出新节点，反驳/质询长出新边；
    每条关系边都标明来源发言人（如「反方二辩 反驳」），自由辩论的交锋指向一目了然。

刻意不把图写在代码里：节点、连线、标签、样式全部遍历 state 现算，
辩论多走一步 state 就多一个节点，前端拿到的永远是"当下这一刻"的图。
"""
from __future__ import annotations

from .debate_state import DebateGraphState, GraphNode, speaker_cn

#: 论证图单节点最多展示的论点字数（控制器里已截断，这里按 SVG 可读性再收一次）
_ARG_LABEL_MAX = 20

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
    "support": "补充", "judge": "评审", "verdict": "宣判", "neutral": "主持",
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


def _node_class(node: GraphNode) -> str:
    if node.camp == "pro":
        return "pro"
    if node.camp == "con":
        return "con"
    return "neutral"


def build_argument_mermaid(state: DebateGraphState, *, added_nodes: list[str] | None = None) -> str:
    """论证关系图：辩题 ↔ 论点节点；支持实线、反驳/质询虚线，当前论点高亮。

    每条边都写明「谁 → 谁 的什么关系」（如 ``反方二辩 反驳``），
    节点副行带发言人、环节与轮次，保证自由辩论的高密度交锋也能读出攻防结构。
    """
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
        sub = f"{kind_cn} · {speaker_cn(node.speaker)} · 第 {node.round} 轮"
        cls = _node_class(node)
        lines.append(f'  {node.id}["{_node_label(head, sub)}"]:::{cls}')

    # 第一条发言与辩题建立引用；其余全靠论点之间的关系边
    first = state.nodes[0].id
    lines.append(f'  T -->|{_escape("切入")}| {first}')

    for edge in state.edges:
        if edge.src not in by_id or edge.dst not in by_id:
            continue
        label = _escape(edge.label)
        if edge.kind == "flow":
            # 主席/评委的时序连线：标签是发言人名，不与"支持/反驳"的语义线混淆
            if label:
                lines.append(f"  {edge.src} -->|{label}| {edge.dst}")
            else:
                lines.append(f"  {edge.src} --> {edge.dst}")
        elif edge.kind == "support":
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


def build_views(state: DebateGraphState, *, added_nodes: list[str] | None = None) -> dict[str, str]:
    """产出视图（供 SSE graph 事件直接使用；键保留 argument，兼容历史前端字段）。"""
    return {"argument": build_argument_mermaid(state, added_nodes=added_nodes)}
