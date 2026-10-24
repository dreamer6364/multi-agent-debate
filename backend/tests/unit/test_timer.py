"""计时模拟单测（TEST_PLAN UT-TIMER-01~05，SPEC §4.2）。"""
from debate.engine.schema import Camp
from debate.engine.sim.timer import (
    NOTE_TIME_UP,
    NOTE_WARNING,
    FreeDebateClock,
    TurnClock,
    truncate_to_sentence,
    words_for,
)


def test_words_for_basic():
    assert words_for(3.0, 220) == 660
    assert words_for(1.5, 220) == 330


def test_warning_fires_once_then_time_up_once():
    clock = TurnClock(660, 220)
    assert clock.consume(549) == []  # 剩 111 > 110，不提醒
    assert clock.consume(1) == [NOTE_WARNING]  # 剩 110 ≤ 110，恰一次
    assert clock.consume(200) == [NOTE_TIME_UP]  # 耗尽叫停
    assert clock.consume(50) == []  # 已提醒/已叫停，不重复


def test_truncate_keeps_last_complete_sentence():
    text = "第一句话。第二句话很长很长很长。第三句话。"
    cut, truncated = truncate_to_sentence(text, 12)
    assert truncated
    assert cut == "第一句话。"


def test_truncate_noop_when_within_budget():
    assert truncate_to_sentence("短句。", 10) == ("短句。", False)


def test_truncate_hard_cut_without_boundary():
    cut, truncated = truncate_to_sentence("没有任何句读符号的一长段文字", 5)
    assert truncated and len(cut) == 5


def test_free_debate_pools_and_turn_cap():
    clock = FreeDebateClock(minutes_per_camp=1.0, speed_cpm=220, turn_cap_minutes=0.5)
    assert clock.turn_cap_words == 110

    assert clock.consume(Camp.CON, 30) == []  # 反方池 220 → 剩 190 > 110
    assert clock.consume(Camp.CON, 80) == [NOTE_WARNING]  # 剩 110，恰触发
    assert clock.consume(Camp.CON, 80) == []
    assert clock.consume(Camp.CON, 30) == ["反方时间用尽"]

    assert clock.consume(Camp.PRO, 50) == []  # 正方池不受反方影响
    assert clock.consume(Camp.PRO, 60) == [NOTE_WARNING]
    assert clock.consume(Camp.PRO, 50) == []
    assert clock.consume(Camp.PRO, 60) == ["正方时间用尽"]
    assert clock.finished
