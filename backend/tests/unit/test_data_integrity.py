"""数据诚信线索抓取（P2-4，守则三/G5 的代码侧兜底，SPEC §4.6）。

代码只抓"待查"线索交内容评委核验，不下造假结论。
"""
from __future__ import annotations

from debate.engine.sim.conduct import flag_unverified_claims

WHITELIST = ["《思考，快与慢》（丹尼尔·卡尼曼）", "世界银行年度报告（World Bank）"]


def test_flags_unsourced_percentage():
    """精确百分比且未标注白名单来源 → 数据待查。"""
    flags = flag_unverified_claims("过度依赖技术的人群比例高达 73.5%，所以应当警惕。", WHITELIST)
    assert flags and flags[0].startswith("数据待查：「")


def test_flags_unsourced_research_claim():
    flags = flag_unverified_claims("研究表明，长期依赖会削弱批判性思维。", WHITELIST)
    assert len(flags) == 1


def test_whitelisted_source_not_flagged():
    """标注了平台白名单来源（书名或作者名）→ 不抓（守则三允许直接引用）。"""
    assert not flag_unverified_claims(
        "正如卡尼曼在《思考，快与慢》中指出，73% 的判断受启发式影响。", WHITELIST)
    assert not flag_unverified_claims(
        "据世界银行年度报告显示，普惠覆盖率提升了 42%。", WHITELIST)


def test_rebuttal_question_not_flagged():
    """反问/质询对方的数据要求不是己方举证 → 不抓。"""
    assert not flag_unverified_claims(
        "请问对方辩友，你们 85% 的数据究竟出自哪里？", WHITELIST)


def test_plain_prose_not_flagged():
    assert not flag_unverified_claims("我方认为依赖不等于失控，这一点历史已经证明。", WHITELIST)
    assert not flag_unverified_claims("", WHITELIST)


def test_max_flags_cap():
    text = "研究表明甲成立。研究表明乙成立。研究表明丙成立。研究表明丁成立。"
    flags = flag_unverified_claims(text, WHITELIST, max_flags=2)
    assert len(flags) == 2
