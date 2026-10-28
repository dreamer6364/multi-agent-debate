"""主席发言守卫（v1.9.2）：必须紧扣辩题、保持中立。

历史缺陷：主席与辩手共用 build_debater_messages，而 NEUTRAL 的立场/要点为空，
提示词里没有【辩题】，引题演讲必然脱题。本组用例锁定修复与三道防线：
①注入辩题与双方立场；②贴题/中立校验（不合格重写一次）；③两次不合格走确定性兜底。
"""
from __future__ import annotations

from types import SimpleNamespace

from debate.engine.agents.context import build_chair_messages
from debate.engine.agents.loader import AgentSpec, load_topics
from debate.engine.nodes.officials import (
    MIN_TOPIC_RATIO,
    make_closing_ceremony_node,
    make_opening_node,
    neutrality_issue,
    topic_drift_ratio,
)
from debate.engine.schema import Camp, Verdict


def _topic(assets_root):
    return load_topics(assets_root)[0]


def _ctx(config, gateway, topic):
    return SimpleNamespace(
        config=config,
        gateway=gateway,
        topic=topic,
        agents={"chair": AgentSpec(
            role_id="chair", camp=Camp.NEUTRAL, position_cn="主席", system_prompt="你是主席，中立。"
        )},
        speeches=[],
        on_speech=None,
        emit=lambda *a, **k: None,
        check_cancel=lambda: None,
        record_degradation=lambda: None,
        degradation_streak=0,
    )


class _SeqGateway:
    """按顺序返回预置文本，并记录每次请求的消息。"""

    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls: list[list[dict]] = []

    def complete(self, messages, **kwargs):  # noqa: ANN001
        self.calls.append(messages)
        text = self.texts.pop(0) if self.texts else "（无文本）"
        return SimpleNamespace(text=text, prompt_tokens=1, completion_tokens=1)


GOOD_KEYNOTE = (
    "今晚的辩题是：人工智能时代，人类应该更依赖技术，还是更保持警惕。"
    "这不是一道关于技术的题，而是一道关于人的题。双方真正的分歧不在于要不要技术，"
    "而在于「依赖」的边界该划在哪里：正方主张更依赖技术，让人腾出手做更值得做的事；"
    "反方主张更保持警惕，为人保留退出、纠错与拒绝的余地。"
    "本场请盯住三个看点：谁的定义更稳、代价由谁承担、效率与自主冲突时谁让步。"
)
GOOD_OPENING = (
    "欢迎来到本场辩论赛。今晚的辩题是：人工智能时代，人类应该更依赖技术，还是更保持警惕。"
    "正方主张更依赖技术，反方主张更保持警惕。本场评委将从逻辑、内容与表达三个维度评分。"
    "各环节限时发言，剩余三十秒有计时提醒，请注意礼仪。现在，比赛开始。"
)
OFF_TOPIC = "今天天气不错，我们不妨聊聊人生与成长：每个人都会在某个时刻怀疑自己走过的路。"


def test_chair_messages_inject_topic_and_both_stances(assets_root):
    """回归：主席消息必须含【辩题】与双方立场（此前 NEUTRAL 导致三者全空）。"""
    topic = _topic(assets_root)
    spec = AgentSpec(role_id="chair", camp=Camp.NEUTRAL, position_cn="主席", system_prompt="你是主席。")
    messages = build_chair_messages(spec=spec, topic=topic, instruction="做开篇引题演讲")
    user = messages[-1]["content"]
    assert "【辩题】" in user and topic.title in user
    assert "【正方立场】" in user and topic.pro_stance in user
    assert "【反方立场】" in user and topic.con_stance in user


def test_topic_drift_ratio_discards_unrelated_speech(assets_root):
    topic = _topic(assets_root)
    assert topic_drift_ratio(GOOD_KEYNOTE, topic) > MIN_TOPIC_RATIO
    assert topic_drift_ratio(OFF_TOPIC, topic) < MIN_TOPIC_RATIO


def test_neutrality_issue_detects_bias_and_one_sided_talk(assets_root):
    topic = _topic(assets_root)
    assert neutrality_issue(GOOD_KEYNOTE) == ""
    assert "倾向性" in neutrality_issue(f"{topic.title}。我方认为依赖技术显然更好。")
    assert "只提及一方" in neutrality_issue(f"今晚的辩题是：{topic.title}。正方主张更依赖技术。")


def test_keynote_rewrites_when_drifting_off_topic(assets_root, config):
    """第一次跑题 → 自动重写（提示带纠偏要求）→ 采用第二次的贴题版本。"""
    gateway = _SeqGateway([OFF_TOPIC, GOOD_KEYNOTE, GOOD_OPENING])
    node = make_opening_node(_ctx(config, gateway, _topic(assets_root)))
    result = node({"transcript": [], "digest": None})
    keynote, opening = result["transcript"]
    assert keynote.text == GOOD_KEYNOTE
    assert "重写要求" in gateway.calls[1][-1]["content"]
    assert opening.text == GOOD_OPENING
    assert len(gateway.calls) == 3  # 引题 2 次（重试）+ 报幕 1 次


def test_keynote_falls_back_when_still_off_topic(assets_root, config):
    """两次跑题 → 确定性兜底：文本必然包含辩题，且标注已兜底，绝不播出跑题内容。"""
    gateway = _SeqGateway([OFF_TOPIC, OFF_TOPIC, GOOD_OPENING])
    node = make_opening_node(_ctx(config, gateway, _topic(assets_root)))
    result = node({"transcript": [], "digest": None})
    keynote = result["transcript"][0]
    assert _topic(assets_root).title in keynote.text
    assert any("兜底" in n for n in keynote.side_notes)


def test_keynote_falls_back_when_one_sided(assets_root, config):
    """贴题但只提正方（不中立）→ 同样被拦下并兜底，兜底文本对双方平衡交代。"""
    topic = _topic(assets_root)
    one_sided = f"今晚的辩题是：{topic.title}。正方主张更依赖技术，这让我们效率更高。"
    gateway = _SeqGateway([one_sided, one_sided, GOOD_OPENING])
    node = make_opening_node(_ctx(config, gateway, topic))
    result = node({"transcript": [], "digest": None})
    keynote = result["transcript"][0]
    assert topic.title in keynote.text
    assert "正方" in keynote.text and "反方" in keynote.text
    assert "我方" not in keynote.text


GOOD_CLOSING = (
    "本场关于人工智能时代，人类应该更依赖技术，还是更保持警惕的讨论到此结束。"
    "正方把技术说成能力的延伸，反方把警惕说成退路的保留，两种提醒都留在场上。"
    "感谢评委团与各位观众，本场比赛结束。"
)


def test_closing_ceremony_speaks_topic_and_stays_neutral(assets_root, config):
    """结束致辞（phase 11）：贴题 + 中立 + 不改判，作为全场最后一条发言。"""
    topic = _topic(assets_root)
    gateway = _SeqGateway([GOOD_CLOSING])
    ctx = _ctx(config, gateway, topic)
    node = make_closing_ceremony_node(ctx)
    verdict = Verdict(votes={Camp.PRO: 2, Camp.CON: 1}, team_totals={Camp.PRO: 90.0, Camp.CON: 83.0},
                      winner=Camp.PRO, best_speaker="pro-1", decisive_rule="majority")
    result = node({"transcript": [], "digest": None, "verdict": verdict})
    closing = result["transcript"][-1]
    assert closing.phase == 11 and closing.speaker == "chair"
    assert closing.text == GOOD_CLOSING
    assert topic_drift_ratio(closing.text, topic) > MIN_TOPIC_RATIO
    assert neutrality_issue(closing.text) == ""
    # 致辞指令必须带上胜方与"不得改判"的硬约束
    instruction = gateway.calls[0][-1]["content"]
    assert "正方" in instruction and "不得改判" in instruction


def test_closing_ceremony_falls_back_off_topic(assets_root, config):
    """致辞两次跑题 → 兜底模板仍贴题、对双方平衡交代。"""
    topic = _topic(assets_root)
    gateway = _SeqGateway([OFF_TOPIC, OFF_TOPIC])
    node = make_closing_ceremony_node(_ctx(config, gateway, topic))
    result = node({"transcript": [], "digest": None, "verdict": None})
    closing = result["transcript"][-1]
    assert topic.title in closing.text
    assert "正方" in closing.text and "反方" in closing.text
    assert any("兜底" in n for n in closing.side_notes)


def test_official_throttle_swaps_and_restores():
    """v2.6 仪式段限速覆盖：进入 swap 成 judges.throttle_seconds，退出恢复全局值。"""
    from debate.engine.nodes.officials import _official_throttle

    gateway = SimpleNamespace(min_interval=15.0)
    ctx = SimpleNamespace(gateway=gateway,
                          config=SimpleNamespace(judges=SimpleNamespace(throttle_seconds=0.0)))
    with _official_throttle(ctx):
        assert gateway.min_interval == 0.0
    assert gateway.min_interval == 15.0

    # 无覆盖配置（None）→ 不动
    gw2 = SimpleNamespace(min_interval=15.0)
    ctx2 = SimpleNamespace(gateway=gw2, config=SimpleNamespace(judges=SimpleNamespace()))
    with _official_throttle(ctx2):
        assert gw2.min_interval == 15.0


def test_closing_ceremony_uses_loose_throttle(assets_root, config):
    """v2.6：评分结束后全局 15s 间隔恢复，致辞首调会白等一轮——节点内必须套用宽松限速。"""
    topic = _topic(assets_root)

    class _ThrottleGw:
        def __init__(self):
            self.min_interval = 15.0
            self.seen: list[float] = []

        def complete(self, messages, **kwargs):  # noqa: ANN001
            self.seen.append(self.min_interval)
            return SimpleNamespace(text=GOOD_CLOSING, prompt_tokens=1, completion_tokens=1)

    gateway = _ThrottleGw()
    ctx = _ctx(config, gateway, topic)
    node = make_closing_ceremony_node(ctx)
    verdict = Verdict(votes={Camp.PRO: 2, Camp.CON: 1}, team_totals={Camp.PRO: 90.0, Camp.CON: 83.0},
                      winner=Camp.PRO, best_speaker="pro-1", decisive_rule="majority")
    node({"transcript": [], "digest": None, "verdict": verdict})

    assert gateway.seen == [config.judges.throttle_seconds]  # 调用发生在覆盖窗口内
    assert gateway.min_interval == 15.0                      # 离开节点恢复全局间隔
