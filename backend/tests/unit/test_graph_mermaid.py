"""辩论图状态与 Mermaid 构建器单测（动态图核心：图必须随发言生长，不是写死的）。

覆盖：
  - 图随每条发言增长（节点/边/版本/回合号推进）
  - 关系语义：支持实线、攻击/质询虚线，首条发言不自连
  - v2.6.2：关系边标明来源发言人（谁指向谁）；中立角色是"主持"而非补充
  - 当前节点高亮、新增节点标记（默认样式）
  - 标签转义：引号/竖线/井号/尖括号不破坏 mermaid 语法
"""
from __future__ import annotations

from debate.engine.graph.debate_state import GraphTracker
from debate.engine.graph.mermaid import build_argument_mermaid
from debate.engine.schema import Camp, Speech


def sp(seq: int, phase: int, name: str, speaker: str, camp: Camp, text: str) -> Speech:
    return Speech(seq=seq, phase=phase, phase_name=name, speaker=speaker, camp=camp, text=text)


def tracker() -> GraphTracker:
    return GraphTracker(topic="人工智能时代，人类应该更依赖技术")


def feed_openings(t: GraphTracker) -> None:
    t.feed_speech(sp(1, 2, "开篇立论", "pro-1", Camp.PRO, "我方认为技术是人的延伸，因为它扩展了能力边界。"))
    t.feed_speech(sp(2, 2, "开篇立论", "con-1", Camp.CON, "然而这种延伸并不成立，技术反而让人退化。"))


def test_empty_state_is_valid_and_minimal():
    t = tracker()
    argument = build_argument_mermaid(t.state)
    assert argument.startswith("flowchart LR")
    assert "人工智能时代" in argument
    # 还没发言时不该凭空长出论点节点
    assert 'n1["' not in argument


def test_graph_grows_with_each_speech():
    """核心断言：图不是一开始就固定，而是每发言一次就多一个节点、多一条边。"""
    t = tracker()
    first = t.feed_speech(sp(1, 2, "开篇立论", "pro-1", Camp.PRO, "我方认为技术是延伸。"))
    assert first["version"] == 1
    assert first["state"]["node_count"] == 1
    assert first["added_nodes"] == ["n1"]
    assert first["added_edges"] == []  # 首条发言无可连对象，不能自连

    second = t.feed_speech(sp(2, 2, "开篇立论", "con-1", Camp.CON, "然而这个前提并不成立。"))
    assert second["version"] == 2
    assert second["state"]["node_count"] == first["state"]["node_count"] + 1
    assert second["state"]["edge_count"] == first["state"]["edge_count"] + 1
    assert second["added_nodes"] == ["n2"]

    # 论证图源码确实多了节点与连线，且边标明"谁指向谁"
    arg = second["views"]["argument"]
    assert 'n2["' in arg
    assert "n2 -.->|反方一辩 反驳| n1" in arg


def test_support_edge_is_solid_attack_is_dashed():
    t = tracker()
    feed_openings(t)
    t.feed_speech(sp(3, 4, "驳论", "pro-2", Camp.PRO, "我方补充一点：工具从来都是人的延伸。"))
    arg = t.history[-1]["views"]["argument"]
    assert "n3 -->|正方二辩 支持| n1" in arg   # 支持：实线
    assert "n2 -.->|反方一辩 反驳| n1" in arg  # 攻击：虚线


def test_question_phase_creates_query_edge():
    t = tracker()
    feed_openings(t)
    t.feed_speech(sp(3, 3, "质询一辩", "con-2", Camp.CON, "请问对方，能力扩展的边界由谁决定？"))
    snap = t.history[-1]
    node = t.state.nodes[-1].to_dict()
    assert node["kind"] == "question"
    assert any(e.kind == "query" for e in t.state.edges)
    assert "-.->|反方二辩 质询|" in snap["views"]["argument"]


def test_free_debate_edges_name_who_points_to_whom():
    """自由辩论（phase 7）：每条交锋边都要写明来源发言人。"""
    t = tracker()
    feed_openings(t)
    t.feed_speech(sp(3, 7, "自由辩论", "pro-2", Camp.PRO, "对方辩友忽略了成本，技术红利其实普惠。"))
    t.feed_speech(sp(4, 7, "自由辩论", "con-2", Camp.CON, "普惠并不意味着没有代价，失业就是代价。"))
    arg = t.history[-1]["views"]["argument"]
    assert "n3 -.->|正方二辩 反驳| n2" in arg
    assert "n4 -.->|反方二辩 反驳| n3" in arg


def test_verdict_marks_converged_and_terminated():
    t = tracker()
    feed_openings(t)
    snap = t.feed_verdict(winner="pro", rule="majority")
    assert snap["converged"] is True
    assert snap["terminated"] is True
    assert snap["state"]["winner"] == "pro"
    assert snap["branch"] == "terminate"


def test_stopped_status_marks_early_finish():
    t = tracker()
    feed_openings(t)
    snap = t.feed_status("stopped")
    assert snap is not None and snap["terminated"] is True
    assert snap["branch"] == "early_finish"
    # 已终止后不再重复补状态
    assert t.feed_status("failed") is None


def test_active_and_new_markers():
    t = tracker()
    snap = t.feed_speech(sp(1, 2, "开篇立论", "pro-1", Camp.PRO, "我方认为技术是延伸。"))
    arg = snap["views"]["argument"]
    assert f"class {snap['active_node']} active" in arg
    assert f"class {snap['active_node']} new" in arg


def test_round_advances_when_both_sides_have_spoken():
    t = tracker()
    feed_openings(t)
    assert t.state.round == 1
    t.feed_speech(sp(3, 4, "驳论", "con-2", Camp.CON, "对方忽略了代价。"))
    assert t.state.round == 1  # 反方连续发言，仍属同一轮
    t.feed_speech(sp(4, 4, "驳论", "pro-2", Camp.PRO, "我方并不认同这个判断。"))
    assert t.state.round == 2


def test_labels_are_escaped_for_mermaid():
    t = tracker()
    t.feed_speech(sp(1, 2, "开篇立论", "pro-1", Camp.PRO,
                     '他说"技术中立" | 但#标签与<script>并不成立。'))
    arg = t.history[-1]["views"]["argument"]
    for bad in ('<script>', '"技术中立"'):
        assert bad not in arg
    assert "#quot;" in arg
    # 每行都是合法语句：节点/连线/类声明，且引号成对闭合（未闭合会截断整张图）
    for line in arg.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        assert stripped.count('"') % 2 == 0, stripped
        assert not stripped.startswith("-"), stripped


def test_state_shape_matches_requirement():
    """需求 1 的状态字段必须齐备（前端据此显示回合/双方 Agent/裁判/分支）。"""
    t = tracker()
    feed_openings(t)
    state = t.history[-1]["state"]
    for key in ("round", "pro_agent", "con_agent", "moderator", "current_claim",
                "converged", "terminated", "branch", "node_count", "edge_count"):
        assert key in state
    assert state["pro_agent"] == "pro-1"
    assert state["con_agent"] == "con-1"
    assert state["moderator"] == "chair"
    assert state["current_claim"].startswith("然而")


def test_history_kept_for_playback():
    """需求 4：保留历史版本，可回看上一回合。"""
    t = tracker()
    feed_openings(t)
    t.feed_phase_end(2)
    assert [s["version"] for s in t.history] == [1, 2, 3]
    assert t.history[0]["round"] == 1
    # 每个版本都自带当时的图（不是共用一个可变字符串）
    assert t.history[0]["views"]["argument"] != t.history[1]["views"]["argument"]


def test_closing_ceremony_is_chair_not_rebuttal():
    """中立角色不该被标成"补充/反驳"：主席致辞是主持，时序边标发言人名。"""
    t = tracker()
    feed_openings(t)
    t.feed_phase_start(11, "主席致辞")
    snap = t.feed_speech(sp(3, 11, "主席致辞", "chair", Camp.NEUTRAL, "感谢双方，本场到此结束。"))
    node = t.state.nodes[-1]
    assert node.kind == "neutral"
    arg = snap["views"]["argument"]
    assert "主持 · 主席" in arg
    assert "n3 -->|主席| n2" in arg  # 中立时序边挂发言人名
    assert "补充" not in arg
