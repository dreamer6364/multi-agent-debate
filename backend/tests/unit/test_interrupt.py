"""打断规则单测（TEST_PLAN UT-INT-01~04，SPEC §4.3）。"""
from debate.engine.schema import Camp, Speech
from debate.engine.sim.interrupt import (
    InterruptLedger,
    apply_interrupt_to_answer,
    parse_interrupt,
)


def test_parse_marker_and_reason():
    clean, reason = parse_interrupt("[打断]请正面回答：你的数据从哪来？后来怎样")
    assert reason == "请正面回答：你的数据从哪来？"  # 句读归入理由
    assert clean == "后来怎样"


def test_parse_full_width_marker():
    clean, reason = parse_interrupt("【打断】请直接回答是或不是。下一问：态度问题。")
    assert reason == "请直接回答是或不是。"  # 句读归入理由
    assert clean.startswith("下一问")


def test_parse_no_marker():
    text = "请正面回答。"
    assert parse_interrupt(text) == (text, None)


def test_ledger_limit_two_per_answer():
    ledger = InterruptLedger(max_per_answer=2)
    assert ledger.try_consume("s1")
    assert ledger.try_consume("s1")
    assert not ledger.try_consume("s1")  # 第三次超限
    assert ledger.try_consume("s2")  # 不同回答独立计数


def test_apply_interrupt_truncates_and_notes():
    original = "第一句完整回答。第二句展开论证展开论证展开论证。第三句补充。"
    answer = Speech(
        seq=5,
        phase=3,
        phase_name="质询一辩",
        speaker="pro-1",
        camp=Camp.PRO,
        text=original,
        word_count=len(original),
    )
    apply_interrupt_to_answer(answer, "请正面回答")
    assert answer.interrupted
    assert "质询方打断：请正面回答" in answer.side_notes
    assert answer.text.endswith("。")
    assert len(answer.text) < len(original)
    assert answer.word_count == len(answer.text)
