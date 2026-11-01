"""回应义务计数（守则四 / G6，SPEC §4.6，P0-3）：同一问题第 3 次追问仍无对应回答 → 回避追问。"""
from __future__ import annotations

from debate.engine.sim.conduct import FollowupLedger, same_question


def test_same_question_detects_rephrasing():
    """换皮追问（改几个字）仍应识别为同一问题。"""
    q1 = "请问对方辩友，技术依赖导致的能力退化有哪一份实证研究支持？"
    q2 = "请对方辩友正面回答：技术依赖造成的能力退化，究竟有什么实证研究支持？"
    assert same_question(q1, q2)


def test_same_question_distinguishes_different_topics():
    q1 = "请问对方辩友，技术依赖导致的能力退化有哪一份实证研究支持？"
    q2 = "那么再请问，效率提升带来的普惠价值又该如何衡量？"
    assert not same_question(q1, q2)


def test_followup_threshold_three_marks_unanswered():
    """同一问题追问 3 次、且回答逐字重复 → 判定"仍无对应回应"。"""
    ledger = FollowupLedger(threshold=3)
    q = "请问对方辩友，能力退化的实证依据到底是什么？"
    evasion = "我方认为这要从整体来看。"

    assert ledger.register_ask(q) == 1
    ledger.register_answer(evasion)
    assert not ledger.is_unanswered(evasion, 1)  # 第 1 次不判

    assert ledger.register_ask(q) == 2
    ledger.register_answer(evasion)
    assert not ledger.is_unanswered(evasion, 2)  # 第 2 次不判

    assert ledger.register_ask(q) == 3
    # 第 3 次追问，回答仍是同一句复读 → 判定回避
    assert ledger.is_unanswered(evasion, 3)


def test_followup_substantive_answer_not_flagged():
    """第 3 次追问但给出新的实质回答 → 不判回避。"""
    ledger = FollowupLedger(threshold=3)
    q = "请问对方辩友，能力退化的实证依据到底是什么？"
    ledger.register_ask(q)
    ledger.register_answer("我方认为这要从整体来看。")
    ledger.register_ask(q)
    ledger.register_answer("我方认为这要从整体来看。")
    assert ledger.register_ask(q) == 3
    assert not ledger.is_unanswered("斯坦福 2023 年的追踪研究显示，基础技能在过度使用中确实出现下滑。", 3)


def test_followup_empty_answer_is_evasion():
    ledger = FollowupLedger(threshold=3)
    q = "这一论点的判准究竟是什么？"
    ledger.register_ask(q)
    ledger.register_answer("……")
    ledger.register_ask(q)
    ledger.register_answer("")
    assert ledger.register_ask(q) == 3
    assert ledger.is_unanswered("   ", 3)  # 空回答


def test_different_questions_kept_in_separate_clusters():
    """不同问题各自计数，互不串扰。"""
    ledger = FollowupLedger(threshold=3)
    q_a = "请问技术依赖的实证依据是什么？"
    q_b = "那么效率提升的普惠价值如何衡量？"
    ledger.register_ask(q_a)
    ledger.register_answer("略。")
    ledger.register_ask(q_b)
    ledger.register_answer("略。")
    # 各自都只被问了 1 次
    assert ledger.register_ask(q_a) == 2
    assert ledger.register_ask(q_b) == 2
