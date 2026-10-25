"""守则违规检测单测（TEST_PLAN UT-CND-01~04，SPEC §4.6）。"""
from debate.engine.sim.conduct import is_hard_evasion, scan, strip_forbidden_format

ABUSE = ["滚", "蠢", "废物", "无耻"]


def test_scan_abuse_hit():
    assert scan("对方辩友简直是诡辩的废物", ABUSE) == ["违规：人身攻击"]


def test_scan_meta_hit():
    assert "违规：跳出角色" in scan("作为AI我将客观分析这道辩题", ABUSE)


def test_scan_clean():
    assert scan("对方辩友的论证存在以偏概全的问题", ABUSE) == []


def test_strip_markdown_and_code_blocks():
    text = "# 观点\n这是正文。\n```python\nprint(1)\n```\n结尾。"
    clean, changed = strip_forbidden_format(text)
    assert changed
    assert "观点" not in clean
    assert "print(1)" not in clean
    assert "这是正文。" in clean and "结尾。" in clean


def test_strip_plain_text_untouched():
    assert strip_forbidden_format("普通发言。") == ("普通发言。", False)


def test_hard_evasion_rules():
    assert is_hard_evasion("   ", ["之前"])  # 空回答
    assert is_hard_evasion("同一条回答", ["另一条", "同一条回答"])  # 逐字重复
    assert not is_hard_evasion("新的正面回应内容", ["旧的"])  # 正常回应


def test_is_repetitive_v15():
    """复读检测（v1.5）：≥15 字连续相同片段判定复读。"""
    from debate.engine.sim.conduct import is_repetitive
    a = "正如弗洛姆在《爱的艺术》中指出，爱是需要学习的能力，包含关心、责任、尊重与了解四个要素。"
    b = "我方再次强调，爱是需要学习的能力，包含关心、责任、尊重与了解四个要素，因此主动去爱更高尚。"
    c = "依恋理论的安全基地效应说明被爱体验的另一面。"
    assert is_repetitive(b, [a])
    assert not is_repetitive(c, [a])
    assert not is_repetitive("短", [a])
