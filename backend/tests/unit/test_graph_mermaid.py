"""辩论图状态与 Mermaid 构建器单测（动态图核心：图必须随发言生长，不是写死的）。

覆盖：
  - 图随每条发言增长（节点/边/版本/回合号推进）
  - 关系语义：支持实线、攻击/质询虚线，首条发言不自连
  - 控制图：裁判判断节点 + 三种分支（继续 / 追加质询 / 提前结束）+ 收敛终止
  - 当前节点高亮、新增节点标记、已完成置灰（默认样式）
  - 标签转义：引号/竖线/井号/尖括号不破坏 mermaid 语法
"""
from __future__ import annotations

from debate.engine.graph.debate_state import GraphTracker
from debate.engine.graph.mermaid import build_argument_mermaid, build_control_mermaid
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
    control = build_control_mermaid(t.state)
    argument = build_argument_mermaid(t.state)
    assert control.startswith("flowchart TD")
    assert argument.startswith("flowchart LR")
    assert "开赛" in control
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

    # 论证图源码确实多了节点与连线
    arg = second["views"]["argument"]
    assert 'n2["' in arg
    assert "n2 -.->|反驳| n1" in arg


def test_support_edge_is_solid_attack_is_dashed():
    t = tracker()
    feed_openings(t)
    t.feed_speech(sp(3, 4, "驳论", "pro-2", Camp.PRO, "我方补充一点：工具从来都是人的延伸。"))
    arg = t.history[-1]["views"]["argument"]
    assert "n3 -->|支持| n1" in arg   # 支持：实线
    assert "n2 -.->|反驳| n1" in arg  # 攻击：虚线


def test_question_phase_creates_query_edge():
    t = tracker()
    feed_openings(t)
    t.feed_speech(sp(3, 3, "质询一辩", "con-2", Camp.CON, "请问对方，能力扩展的边界由谁决定？"))
    snap = t.history[-1]
    node = t.state.nodes[-1].to_dict()
    assert node["kind"] == "question"
    assert any(e.kind == "query" for e in t.state.edges)
    assert "-.->|质询|" in snap["views"]["argument"]


def test_control_graph_has_judge_and_branch_nodes():
    t = tracker()
    feed_openings(t)
    snap = t.feed_phase_end(2)
    control = snap["views"]["control"]
    assert "裁判判断" in control
    assert "继续下一轮" in control
    assert 'f{"' in control or '{"' in control  # 菱形判断节点
    assert snap["branch"] == "next_round"


def test_question_phase_end_branches_to_extra_query():
    t = tracker()
    feed_openings(t)
    snap = t.feed_phase_end(3)
    assert "追加质询" in snap["views"]["control"]
    assert snap["branch"] == "extra_query"


def test_verdict_converges_and_terminates():
    t = tracker()
    feed_openings(t)
    snap = t.feed_verdict(winner="pro", rule="majority")
    assert snap["converged"] is True
    assert snap["terminated"] is True
    assert "已收敛" in snap["views"]["control"]
    assert "终止 · 正方胜" in snap["views"]["control"]
    assert snap["state"]["winner"] == "pro"
    assert snap["branch"] == "terminate"


def test_stopped_status_marks_early_finish():
    t = tracker()
    feed_openings(t)
    snap = t.feed_status("stopped")
    assert snap is not None and snap["terminated"] is True
    assert "提前结束" in snap["views"]["control"]
    assert snap["branch"] == "early_finish"
    # 已终止后不再重复补终止节点
    assert t.feed_status("failed") is None


def test_active_and_new_markers():
    t = tracker()
    snap = t.feed_speech(sp(1, 2, "开篇立论", "pro-1", Camp.PRO, "我方认为技术是延伸。"))
    arg = snap["views"]["argument"]
    assert f"class {snap['active_node']} active" in arg
    assert f"class {snap['active_node']} new" in arg
    control = snap["views"]["control"]
    assert f"class {snap['active']} active" in control


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
                "converged", "terminated", "branch", "node_count", "edge_count", "flow_count"):
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


def test_terminate_stays_last_after_closing_ceremony():
    """宣判后还有主席致辞：终止节点必须仍是控制图最后一个节点，且是唯一高亮项。

    此前收尾环节直接 append，终止被夹在中间，看起来像"比赛结束了还在调度"。
    """
    t = tracker()
    feed_openings(t)
    t.feed_phase_end(2)
    t.feed_phase_start(10, "宣判闭幕")
    t.feed_verdict(winner="pro", rule="majority")
    t.feed_phase_start(11, "主席致辞")
    t.feed_speech(sp(3, 11, "主席致辞", "chair", Camp.NEUTRAL, "感谢双方，本场到此结束。"))
    snap = t.feed_phase_end(11)

    kinds = [f.kind for f in t.state.flow]
    assert kinds[-1] == "terminate", kinds
    active = [f.kind for f in t.state.flow if f.state == "active"]
    assert active == ["terminate"], active
    control = snap["views"]["control"]
    assert "主席致辞" in control and "终止" in control
    # 终止出现在致辞之后的那一段文字里，顺序不能反
    assert control.index("主席致辞") < control.index("终止")


def test_closing_ceremony_is_chair_not_rebuttal():
    """中立角色不该被标成"补充/反驳"：主席致辞是主持，不是支持某个论点。"""
    t = tracker()
    feed_openings(t)
    t.feed_phase_start(11, "主席致辞")
    snap = t.feed_speech(sp(3, 11, "主席致辞", "chair", Camp.NEUTRAL, "感谢双方，本场到此结束。"))
    node = t.state.nodes[-1]
    assert node.kind == "neutral"
    assert "主持" in snap["views"]["control"]
    assert "补充" not in snap["views"]["control"]
