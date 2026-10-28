"""复盘泳道总结单测（v2.7）：阶段 × 阵营泳道 + 公共场域。

覆盖：
  - 立论/驳论/结辩逐条条目：动作、内容摘录、指向/被指向与论证关系图同源
  - 自由辩论按阵营合并轮换条目（顺序链 + 点对点指向）
  - 「被指向」：早前立论在本环节被攻击时，受害者阵营出现补注条目
  - 公共场域：接收方/计时提示按环节；违规打断与质检谬误进特殊标注
  - 中立发言（主席/评委）不进阵营泳道，只进公共标注
"""
from __future__ import annotations

from types import SimpleNamespace

from debate.engine.schema import Camp, Speech
from debate.engine.summary import build_swimlane


def sp(seq, phase, name, speaker, camp, text, **over):
    return Speech(seq=seq, phase=phase, phase_name=name, speaker=speaker,
                  camp=camp, text=text, **over)


def sample_speeches() -> list:
    return [
        sp(1, 2, "开篇立论", "pro-1", Camp.PRO, "我方认为技术是人的延伸，它扩展了能力边界。"),
        sp(2, 2, "开篇立论", "con-1", Camp.CON, "然而这种延伸并不成立，技术反而让人退化。"),
        sp(3, 4, "驳论", "con-2", Camp.CON, "对方的延伸论恰恰忽略了自己的主动性。"),
        sp(4, 4, "驳论", "pro-2", Camp.PRO, "对方偷换了概念，延伸从来不等于依赖。"),
        sp(5, 7, "自由辩论", "pro-2", Camp.PRO, "请问对方，退化的判准是什么？"),
        sp(6, 7, "自由辩论", "con-2", Camp.CON, "判准就是自主性的流失，我方并未偷换。"),
        sp(7, 8, "总结陈词", "pro-4", Camp.PRO, "综上，我方重申判准：技术延伸服务人的主动。"),
    ]


def test_closing_action_is_summary():
    payload = build_swimlane(sample_speeches())
    row8 = payload["rows"][3]
    assert row8["pro"][0]["action"] == "总结陈词"
    assert row8["pro"][0]["speaker"] == "正方四辩"


def test_basic_rows_follow_phase_order():
    payload = build_swimlane(sample_speeches(), topic="技术是人的延伸")
    assert payload["topic"] == "技术是人的延伸"
    assert [r["phase"] for r in payload["rows"]] == [2, 4, 7, 8]
    assert [r["name"] for r in payload["rows"]][0] == "开篇立论"


def test_statement_entry_action_and_content():
    payload = build_swimlane(sample_speeches())
    row2 = payload["rows"][0]
    entry = row2["pro"][0]
    assert entry["speaker"] == "正方一辩"
    assert entry["action"] == "开篇立论"
    assert entry["content"].startswith("我方认为")
    # 正方一辩立论先后被反方一辩/二辩攻击 → 被指向与图同源
    assert any("反方一辩" in p and "反驳" in p for p in entry["pointed_by"])
    # 反方一辩开篇带攻击词 → 分类为反驳，指向正方一辩
    con_entry = row2["con"][0]
    assert con_entry["action"] == "反驳拆解"
    assert any("正方一辩" in p for p in con_entry["points_to"])


def test_rebuttal_points_to_original_claim():
    payload = build_swimlane(sample_speeches())
    row4 = payload["rows"][1]
    con_entry = row4["con"][0]
    assert con_entry["action"] in ("反驳拆解", "立论主张")
    assert any("正方" in p for p in con_entry["points_to"]), con_entry


def test_victim_annotation_in_attacker_phase():
    """正方一辩立论被反方二辩在驳论阶段攻击 → 驳论行正方格出现「被指向」条目。"""
    payload = build_swimlane(sample_speeches())
    row4 = payload["rows"][1]
    victim = [e for e in row4["pro"] if e["action"] == "被指向"]
    assert victim, "受害者阵营应有被指向条目"
    assert victim[0]["speaker"] == "正方一辩"
    assert "反方二辩" in victim[0]["content"]
    # 同环节内的攻击不产生「被指向」补注（攻击者自己的 points_to 已体现）
    # 跨环节攻击见 test_free_debate_grouped_per_camp：自由辩攻击驳论论点 → 反方格有补注


def test_free_debate_grouped_per_camp():
    payload = build_swimlane(sample_speeches())
    row7 = payload["rows"][2]
    # 各自合并成轮换条目；反方另有「被指向」（其驳论被正方在自由辩中攻击）
    assert len(row7["pro"]) == 1
    pro = row7["pro"][0]
    assert pro["speaker"] == "正方轮换"
    assert "共 1 条交锋" in pro["content"]
    assert "正方二辩" in pro["content"]
    assert pro["points_to"] == ["点对点针对对方上一位发言辩手"]
    assert any(e["action"] == "被指向" for e in row7["con"])
    assert row7["public"]["timer"] == "逐轮独立计时"


def test_public_notes_cover_interrupt_and_fallacy():
    # Speech schema 不带 fallacies 字段，与 REST 一样用 SimpleNamespace 挂载
    first = SimpleNamespace(
        seq=1, phase=2, phase_name="开篇立论", speaker="pro-1", camp=Camp.PRO,
        text="我方认为技术是延伸。", side_notes=["违规：非质询环节打断"],
        interrupted=True, fallacies=[{"name": "以偏概全", "quote": "x", "explanation": "y"}],
    )
    second = sp(2, 2, "开篇立论", "con-1", Camp.CON, "然而并不成立。")
    payload = build_swimlane([first, second])
    notes = payload["rows"][0]["public"]["notes"]
    assert any("正方一辩 违规打断" in n for n in notes)
    assert any("正方一辩 被质检标注「以偏概全」" in n for n in notes)


def test_neutral_speech_goes_to_public_not_camps():
    speeches = [
        sp(1, 1, "主席开场", "chair", Camp.NEUTRAL, "欢迎双方辩手，比赛开始。"),
        sp(2, 2, "开篇立论", "pro-1", Camp.PRO, "我方认为技术是延伸。"),
    ]
    payload = build_swimlane(speeches)
    row1 = payload["rows"][0]
    assert row1["pro"] == [] and row1["con"] == []
    assert any("主席" in n for n in row1["public"]["notes"])


def test_empty_speeches_yield_empty_rows():
    assert build_swimlane([], topic="t") == {"topic": "t", "rows": []}


def test_simple_namespace_transcript_supported():
    """REST 详情用 SimpleNamespace 包装落库行，本函数必须兼容鸭子类型。"""
    rows = [SimpleNamespace(
        seq=1, phase=2, phase_name="开篇立论", speaker="pro-1", camp="pro",
        text="我方认为技术是延伸。", side_notes=[], interrupted=False, fallacies=[],
    )]
    payload = build_swimlane(rows, topic="t")
    assert payload["rows"][0]["pro"][0]["speaker"] == "正方一辩"
