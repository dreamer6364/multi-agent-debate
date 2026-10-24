"""上下文组装单测（TEST_PLAN UT-CTX-01~04，SPEC §4.4 / AGENT_CONSTRAINTS §6）。"""
import pytest

from debate.engine.agents.context import (
    build_debater_messages,
    build_judge_messages,
    estimate_tokens,
)
from debate.engine.agents.loader import load_agents, load_topics
from debate.engine.schema import Camp, Speech


@pytest.fixture(scope="module")
def agents(assets_root):
    return load_agents(assets_root)


@pytest.fixture(scope="module")
def topics(assets_root):
    return load_topics(assets_root)


def mk_speeches(count: int) -> list[Speech]:
    return [
        Speech(
            seq=i + 1, phase=2, phase_name="开篇立论",
            speaker="pro-1" if i % 2 == 0 else "con-1",
            camp=Camp.PRO if i % 2 == 0 else Camp.CON,
            text=f"第{i + 1}条发言内容，长度可观，便于检验注入与裁剪。" * 3,
            word_count=60,
        )
        for i in range(count)
    ]


def test_five_blocks_camp_isolation_and_recent_k(agents, topics):
    topic = topics[0]
    messages = build_debater_messages(
        spec=agents["pro-1"], topic=topic, digest_text="摘要内容",
        recent=mk_speeches(8), instruction="请立论", recent_k=6,
    )
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    content = messages[1]["content"]
    for block in ("【本方立场】", "【战局摘要】", "【最近发言】", "【本环节任务】"):
        assert block in content
    assert topic.pro_points[0] in content  # 注入本方要点
    assert topic.con_points[0] not in content  # 立场信息隔离：对方要点不可见
    assert "[3]" in content and "[8]" in content and "[1]" not in content  # 只留最近 6 条


def test_trim_order_recent_then_digest(agents, topics):
    topic = topics[0]
    messages = build_debater_messages(
        spec=agents["pro-1"], topic=topic, digest_text="摘" * 500,
        recent=mk_speeches(6), instruction="请立论", recent_k=6,
        max_input_tokens=600,
    )
    content = messages[1]["content"]
    assert "【最近发言】\n（暂无）" in content  # 第一刀：砍最近原文
    assert content.count("摘") < 500  # 第二刀：digest 被压缩
    assert topic.pro_points[0] in content  # 要点最后才动


def test_judge_context_contains_both_sides_and_pool(agents, topics):
    topic = topics[0]
    messages = build_judge_messages(
        spec=agents["judge-logic"], topic=topic, digest_text="全场摘要",
        excerpts=[], checklist="各环节要点", instruction="请打分",
    )
    content = messages[1]["content"]
    assert topic.pro_points[0] in content and topic.con_points[0] in content  # 评委回双可见
    assert "【原文摘录】" in content and "【各环节要点清单】" in content


def test_estimate_tokens_conservative():
    assert estimate_tokens("") >= 1
    assert estimate_tokens("一二三") == 3
